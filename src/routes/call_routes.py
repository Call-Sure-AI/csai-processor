from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database.config import SessionLocal
from database.models import Call, ConversationTurn
from services.telephony import get_telephony_provider
from services.speech.deepgram_ws_service import DeepgramWebSocketService
from services.rag.rag_service import get_rag_service
from services.agent_config_service import agent_config_service
from services.prompt_template_service import prompt_template_service
from services.company_service import company_service
from config.settings import settings
from datetime import datetime
import logging
import json
import asyncio

logger = logging.getLogger(__name__)

router = APIRouter()

# Shared context storage
call_context = {}
agent_cache = {}
AGENT_CACHE_TTL = 300
interrupted_text_storage = {}

from_number_global = None
to_number_global = None


@router.post("/incoming")
async def handle_incoming_call(
    request: Request,
    provider: str = Query(default="twilio")
):
    """
    Unified incoming call handler supporting multiple providers
    """
    global from_number_global, to_number_global
    
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        from_number_global = from_number
        to_number_global = to_number
        
        company_id = request.query_params.get("company_id")
        agent_id = request.query_params.get("agent_id")
        
        logger.info(f"INCOMING CALL - CallSid: {call_sid}")
        logger.info(f"From: {from_number}, To: {to_number}")
        logger.info(f"Company: {company_id}, Agent: {agent_id}, Provider: {provider}")
        
        # Store context for WebSocket
        call_context[call_sid] = {
            "company_id": company_id,
            "agent_id": agent_id,
            "from_number": from_number,
            "to_number": to_number
        }
        
        # Get provider instance
        telephony = get_telephony_provider(provider)
        
        # Generate WebSocket URL
        websocket_url = f"/api/v1/calls/media-stream?call_sid={call_sid}&provider={provider}"
        
        # Generate provider-specific response
        response_xml = telephony.generate_connection_response(websocket_url, call_context[call_sid])
        
        return Response(content=response_xml, media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error handling incoming call: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        # Return generic error response
        error_response = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">An error occurred. Please try again.</Say>
</Response>"""
        return Response(content=error_response, media_type="application/xml")


@router.websocket("/media-stream")
async def handle_media_stream(
    websocket: WebSocket,
    provider: str = Query(default="twilio")
):
    """
    Unified WebSocket handler for call media streaming with full DB and S3 support
    """
    global from_number_global, to_number_global
    
    try:
        await websocket.accept()
        logger.info(f"WebSocket ACCEPTED for provider: {provider}")
    except Exception as e:
        logger.error(f"Failed to accept WebSocket: {str(e)}")
        return
    
    conversation_transcript = []
    call_sid = websocket.query_params.get("call_sid")
    first_message_data = None
    stop_audio_flag = {"stop": False}
    current_audio_task = None
    greeting_sent = False
    greeting_start_time = None
    is_agent_speaking_ref = {"speaking": False}
    current_audio_task_ref = {"task": None}
    
    # Get telephony provider
    telephony = get_telephony_provider(provider)
    
    # Extract call_sid from first message if not in query params
    if not call_sid:
        logger.info("No call_sid in query, waiting for start event...")
        try:
            for attempt in range(3):
                message = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                data = json.loads(message)
                event_type = data.get("event")
                
                if event_type == "connected":
                    continue
                elif event_type == "start":
                    call_sid = data.get("start", {}).get("callSid")
                    first_message_data = data
                    break
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for start event")
            await websocket.close(code=1008)
            return
    
    if not call_sid:
        logger.error("Could not obtain call_sid")
        await websocket.close(code=1008)
        return
    
    logger.info(f"Call SID validated: {call_sid}")
    
    # Get call context
    context = call_context.get(call_sid, {})
    company_id = context.get("company_id")
    master_agent_id = context.get("agent_id")
    
    # Fetch agent configuration
    try:
        master_agent = await agent_config_service.get_master_agent(company_id, master_agent_id)
        company_name = company_service.get_company_name_by_id(company_id)
    except Exception as e:
        logger.error(f"Failed to fetch agent config: {e}")
        await websocket.close(code=1008)
        return
    
    if not master_agent:
        logger.error(f"Master agent {master_agent_id} not found!")
        await websocket.close(code=1008, reason="Master agent not found")
        return
    
    agent_name = master_agent["name"]
    current_agent_context = master_agent
    call_metadata = {
        "start_time": datetime.utcnow(),
        "from_number": from_number_global,
        "to_number": to_number_global,
        "company_id": company_id,
        "agent_id": master_agent_id,
        "provider": provider
    }
    
    # Generate greeting
    greeting = prompt_template_service.generate_greeting(master_agent, company_id, agent_name)
    logger.info(f"Greeting pre-generated: {greeting[:50]}...")
    
    # Initialize services
    db = SessionLocal()
    deepgram_service = DeepgramWebSocketService()
    rag = get_rag_service()
    stream_id = None
    call_state = {"first_interaction": True, "interaction_count": 0}
    
    # CREATE INITIAL CALL RECORD IN DATABASE
    try:
        new_call = Call(
            call_sid=call_sid,
            company_id=company_id,
            from_number=from_number_global,
            to_number=to_number_global,
            status="in-progress",
            created_at=datetime.utcnow(),
            provider=provider
        )
        db.add(new_call)
        db.commit()
        logger.info(f"Call record created in DB: {call_sid}")
    except Exception as e:
        logger.error(f"Failed to create call record: {e}")
        db.rollback()
    
    try:
        # Define callbacks
        async def on_interim_transcript(session_id: str, transcript: str, confidence: float):
            """Handle interim transcripts for interruption"""
            nonlocal stream_id, current_audio_task_ref
            
            word_count = len(transcript.split())
            if word_count < 2:
                return
            
            is_speaking = is_agent_speaking_ref["speaking"]
            logger.info(f"🎧 INTERRUPT CHECK: {transcript[:30]} | is_speaking={is_speaking}")
            
            if is_speaking:
                logger.warning(f"INTERRUPTING AGENT! {transcript}")
                stop_audio_flag["stop"] = True
                is_agent_speaking_ref["speaking"] = False
                
                # Send clear command
                await telephony.send_clear_command(websocket, stream_id)
                
                # Cancel audio task
                if current_audio_task_ref["task"] and not current_audio_task_ref["task"].done():
                    current_audio_task_ref["task"].cancel()
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(current_audio_task_ref["task"]),
                            timeout=0.3
                        )
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                    current_audio_task_ref["task"] = None
            
            # Save interrupted text
            interrupted_text_storage[call_sid] = {
                "text": transcript,
                "timestamp": datetime.utcnow(),
                "confidence": confidence
            }
        
        async def on_deepgram_transcript(session_id: str, transcript: str):
            """Handle final transcripts"""
            nonlocal conversation_transcript, greeting_start_time
            
            if not transcript.strip():
                return
            
            # Echo protection
            if greeting_start_time:
                elapsed = (datetime.utcnow() - greeting_start_time).total_seconds()
                if elapsed < 1.5:
                    logger.debug(f"Ignoring early transcript echo: {transcript}")
                    return
            
            # Check for interrupted text
            if call_sid in interrupted_text_storage:
                interrupted = interrupted_text_storage.pop(call_sid)
                interrupted_text = interrupted["text"]
                age = (datetime.utcnow() - interrupted["timestamp"]).total_seconds()
                
                if age < 10.0:  # Only use if recent
                    if interrupted_text.lower() not in transcript.lower():
                        transcript = f"{interrupted_text} {transcript}"
                        logger.info(f"Combined with interrupted text: {transcript}")
                    elif len(transcript.split()) < 3 and len(interrupted_text.split()) >= 3:
                        transcript = interrupted_text
                        logger.info(f"Using interrupted text instead: {transcript}")
            
            logger.info(f"👤 CUSTOMER SAID: {transcript}")
            conversation_transcript.append({
                "role": "user",
                "content": transcript,
                "timestamp": datetime.utcnow().isoformat()
            })
            
            # Save to database (background)
            asyncio.create_task(save_to_db_background(call_sid, "user", transcript))
            
            # Process and respond
            is_agent_speaking_ref["speaking"] = True
            stop_audio_flag["stop"] = False
            
            try:
                await process_and_respond_incoming(
                    transcript=transcript,
                    websocket=websocket,
                    stream_id=stream_id,
                    stop_audio_flag=stop_audio_flag,
                    db=db,
                    call_sid=call_sid,
                    current_agent_context=current_agent_context,
                    current_agent_id=master_agent_id,
                    company_id=company_id,
                    conversation_transcript=conversation_transcript,
                    call_metadata=call_metadata,
                    is_speaking_ref=is_agent_speaking_ref,
                    audio_task_ref=current_audio_task_ref,
                    telephony=telephony
                )
                logger.info("Response completed")
            except asyncio.CancelledError:
                logger.info("Response cancelled by interruption")
            except Exception as e:
                logger.error(f"Response error: {e}")
            finally:
                is_agent_speaking_ref["speaking"] = False
                current_audio_task_ref["task"] = None
        
        # Initialize Deepgram
        session_id = f"deepgram_{call_sid}"
        deepgram_init_task = asyncio.create_task(
            deepgram_service.initialize_session(
                session_id=session_id,
                callback=on_deepgram_transcript,
                interruption_callback=on_interim_transcript
            )
        )
        
        # Send greeting immediately
        if first_message_data and first_message_data.get("event") == "start":
            stream_id = first_message_data.get("streamSid")
            logger.info(f"📡 Stream started: {stream_id}")
            
            is_agent_speaking_ref["speaking"] = True
            stop_audio_flag["stop"] = False
            greeting_start_time = datetime.utcnow()
            
            # Add greeting to transcript
            conversation_transcript.append({
                "role": "assistant",
                "content": greeting,
                "timestamp": datetime.utcnow().isoformat()
            })
            asyncio.create_task(save_to_db_background(call_sid, "assistant", greeting))
            
            current_audio_task = asyncio.create_task(
                telephony.stream_audio_to_call(
                    websocket, stream_id, greeting,
                    stop_audio_flag, is_agent_speaking_ref
                )
            )
            current_audio_task_ref["task"] = current_audio_task
            
            try:
                await current_audio_task
            except asyncio.CancelledError:
                pass
            finally:
                is_agent_speaking_ref["speaking"] = False
                current_audio_task_ref["task"] = None
            
            greeting_sent = True
        
        # Wait for Deepgram to be ready
        deepgram_connected = await deepgram_init_task
        if not deepgram_connected:
            logger.error("Deepgram connection failed")
            await websocket.close()
            return
        
        logger.info("Deepgram ready - entering message loop")
        
        # Main message loop
        await telephony.handle_media_stream(websocket, deepgram_service, stream_id, session_id)
        
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        logger.info(f"Cleaning up {call_sid}")
        
        # SAVE CALL DATA TO S3 AND UPDATE DATABASE
        try:
            call_duration = 0
            if call_metadata.get("start_time"):
                call_duration = int((datetime.utcnow() - call_metadata["start_time"]).total_seconds())
            
            # Upload transcript to S3
            logger.info(f"Uploading call data to S3...")
            s3_urls = await call_recording_service.save_call_data(
                call_sid=call_sid,
                company_id=company_id,
                agent_id=master_agent_id,
                transcript=conversation_transcript,
                recording_url=None,  # Add if you have recording URL
                duration=call_duration,
                from_number=call_metadata.get("from_number"),
                to_number=call_metadata.get("to_number")
            )
            
            # Update Call record in database
            try:
                call_record = db.query(Call).filter_by(call_sid=call_sid).first()
                if call_record:
                    call_record.transcription = s3_urls.get("transcript_url")
                    call_record.company_id = company_id
                    call_record.from_number = call_metadata.get("from_number")
                    call_record.to_number = call_metadata.get("to_number")
                    call_record.duration = call_duration
                    call_record.status = "completed"
                    call_record.ended_at = datetime.utcnow()
                    call_record.provider = provider
                    db.commit()
                    logger.info(f"Call record updated in DB with S3 URLs")
                else:
                    logger.warning(f"Call record not found for {call_sid}")
            except Exception as db_error:
                logger.error(f"Database update error: {db_error}")
                db.rollback()
                
        except Exception as s3_error:
            logger.error(f"S3 upload error: {s3_error}")
        
        # Clean up
        call_context.pop(call_sid, None)
        interrupted_text_storage.pop(call_sid, None)
        
        try:
            await deepgram_service.close_session(session_id)
        except:
            pass
        
        db.close()
        logger.info(f"Cleanup complete for {call_sid}")


async def process_and_respond_incoming(
    transcript: str,
    websocket: WebSocket,
    stream_id: str,
    stop_audio_flag: dict,
    db: Session,
    call_sid: str,
    current_agent_context: dict,
    current_agent_id: str,
    company_id: str,
    conversation_transcript: list,
    call_metadata: dict,
    is_speaking_ref: dict,
    audio_task_ref: dict,
    telephony
):
    """
    Process incoming call with AI-powered response generation
    """
    from services.rag.rag_service import get_rag_service
    from services.rag_routing_service import rag_routing_service
    
    rag = get_rag_service()
    
    try:
        # Detect sentiment
        sentiment_analysis = prompt_template_service.detect_sentiment_and_urgency(
            transcript, current_agent_context
        )
        logger.info(f"Sentiment: {sentiment_analysis.get('sentiment')}, Urgency: {sentiment_analysis.get('urgency')}")
        
        # AI-powered routing decision
        routing_decision = await rag_routing_service.should_retrieve_documents(
            user_message=transcript,
            conversation_history=conversation_transcript,
            call_type="incoming",
            agent_context=current_agent_context
        )
        
        response_strategy = routing_decision.get("response_strategy")
        logger.info(f"AI Routing: {response_strategy}")
        
        # Build conversation context
        conversation_messages = []
        for msg in conversation_transcript[-10:]:
            if msg["role"] in ["user", "assistant"]:
                conversation_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
        
        # Generate response based on strategy
        if response_strategy == "direct_canned":
            # Simple direct response
            simple_prompt = [
                {"role": "system", "content": f"You are a helpful assistant. Respond naturally to this greeting/farewell in 1 sentence."},
                {"role": "user", "content": transcript}
            ]
            response_chunks = []
            async for chunk in rag.llm.astream(simple_prompt):
                if chunk.content:
                    response_chunks.append(chunk.content)
            llm_response = "".join(response_chunks)
            
        elif response_strategy == "conversation_context":
            # Use conversation context only
            support_context = f"""INCOMING SUPPORT CALL - ACTIVE CONVERSATION
Customer's Sentiment: {sentiment_analysis.get('sentiment', 'neutral')}
Urgency: {sentiment_analysis.get('urgency', 'normal')}
Customer's Current Message: {transcript}

You are continuing an active conversation. Use the conversation history to respond naturally.
GUIDELINES:
- Reference what was already discussed
- Keep responses natural and conversational (2-4 sentences typical)
- Be helpful and empathetic"""
            
            conversation_messages.insert(0, {"role": "system", "content": support_context})
            response = await rag.llm_with_functions.ainvoke(conversation_messages)
            
            if hasattr(response, 'additional_kwargs') and 'function_call' in response.additional_kwargs:
                function_call = response.additional_kwargs['function_call']
                function_name = function_call['name']
                arguments = json.loads(function_call['arguments'])
                
                from services.agent_tools import execute_function
                llm_response = await execute_function(
                    function_name=function_name,
                    arguments=arguments,
                    company_id=company_id,
                    call_sid=call_sid or "unknown",
                    campaign_id=None,
                    user_timezone=call_metadata.get("user_timezone", "UTC") if call_metadata else "UTC",
                    business_hours={"start": "09:00", "end": "18:00"}
                )
            else:
                llm_response = response.content
                
        elif response_strategy == "document_retrieval":
            # Full RAG with document retrieval
            support_context = f"""INCOMING SUPPORT CALL
Customer's Sentiment: {sentiment_analysis.get('sentiment', 'neutral')}
Urgency: {sentiment_analysis.get('urgency', 'normal')}
Customer's Current Message: {transcript}

You are on a LIVE SUPPORT PHONE CALL. Use the provided documentation to give accurate answers.
RESPONSE GUIDELINES:
- If customer asks for details, provide them completely
- Match the customer's urgency and tone
- Be solution-focused and clear"""
            
            conversation_messages.insert(0, {"role": "system", "content": support_context})
            
            response_chunks = []
            async for chunk in rag.get_answer(
                company_id=company_id,
                question=transcript,
                agent_id=current_agent_id,
                call_sid=call_sid,
                conversation_context=conversation_messages,
                call_type="incoming"
            ):
                response_chunks.append(chunk)
            llm_response = "".join(response_chunks)
        else:
            llm_response = "I'm here to help. Could you tell me more about what you need?"
        
        # Add to transcript
        conversation_transcript.append({
            "role": "assistant",
            "content": llm_response,
            "timestamp": datetime.utcnow().isoformat(),
            "strategy": response_strategy
        })
        
        logger.info(f"AGENT: {llm_response[:100]}...")
        
        # Save to DB (background)
        asyncio.create_task(save_to_db_background(call_sid, "assistant", llm_response))
        
        # Stream audio response
        if is_speaking_ref:
            is_speaking_ref["speaking"] = True
        
        audio_task = asyncio.create_task(
            telephony.stream_audio_to_call(
                websocket, stream_id, llm_response,
                stop_audio_flag, is_speaking_ref
            )
        )
        
        if audio_task_ref:
            audio_task_ref["task"] = audio_task
        
        try:
            await audio_task
        except asyncio.CancelledError:
            logger.info("Audio cancelled by interruption")
            raise
        finally:
            if is_speaking_ref:
                is_speaking_ref["speaking"] = False
            if audio_task_ref:
                audio_task_ref["task"] = None
                
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        # Error recovery response
        error_msg = "I'm having trouble. Could you please repeat?"
        await telephony.stream_audio_to_call(
            websocket, stream_id, error_msg,
            stop_audio_flag, is_speaking_ref
        )



def save_to_db_background(call_sid: str, role: str, content: str):
    """Fire-and-forget DB write"""
    async def save():
        db = None
        try:
            db = SessionLocal()
            db.add(ConversationTurn(
                call_sid=call_sid,
                role=role,
                content=content,
                created_at=datetime.utcnow()
            ))
            db.commit()
        except Exception as e:
            logger.error(f"Background DB error: {e}")
            if db:
                db.rollback()
        finally:
            if db:
                db.close()
    
    asyncio.create_task(save())


@router.post("/outbound")
async def initiate_outbound_call(
    request: Request,
    provider: str = Query(default="twilio")
):
    """Initiate outbound call"""
    try:
        data = await request.json()
        
        telephony = get_telephony_provider(provider)
        
        result = await telephony.initiate_call(
            from_number=data["from_number"],
            to_number=data["to_number"],
            webhook_url=data["webhook_url"],
            status_callback_url=data.get("status_callback_url"),
            record=data.get("record", False)
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Outbound call failed: {str(e)}")
        return {"error": str(e)}, 500

@router.post("/status")
async def handle_call_status(request: Request):
    """
    Handle call status callbacks from Twilio/Exotel
    """
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid") or form_data.get("Sid")
        call_status = form_data.get("CallStatus") or form_data.get("Status")
        
        logger.info(f"Call status update: {call_sid} - {call_status}")
        
        # Update database asynchronously
        async def update():
            db = None
            try:
                db = SessionLocal()
                call_record = db.query(Call).filter_by(call_sid=call_sid).first()
                
                if call_record:
                    call_record.status = call_status
                    if call_status in ["completed", "failed", "busy", "no-answer"]:
                        call_record.ended_at = datetime.utcnow()
                    db.commit()
                    logger.info(f"Updated call status in DB: {call_sid}")
                else:
                    logger.warning(f"Call record not found: {call_sid}")
                    
            except Exception as e:
                logger.error(f"DB error: {e}")
                if db:
                    db.rollback()
            finally:
                if db:
                    db.close()
        
        asyncio.create_task(update())
        
        return {"status": "ok"}
        
    except Exception as e:
        logger.error(f"Error handling call status: {e}")
        return {"status": "error", "message": str(e)}
