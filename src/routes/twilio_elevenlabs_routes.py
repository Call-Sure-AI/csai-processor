# src\routes\twilio_elevenlabs_routes.py
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database.config import SessionLocal
from database.models import CallType, ConversationTurn
from services.speech.deepgram_ws_service import DeepgramWebSocketService
from services.voice.elevenlabs_service import elevenlabs_service
from services.rag.rag_service import get_rag_service
from services.rag_routing_service import rag_routing_service
from services.datetime_parser_service import datetime_parser_service
from services.booking_orchestration_service import booking_orchestrator, BookingState
from twilio.twiml.voice_response import VoiceResponse, Connect
from datetime import datetime, timedelta
from config.settings import settings
from database.config import get_db
import logging
import json
import asyncio
from services.intent_router_service import intent_router_service
from services.agent_config_service import agent_config_service
from services.prompt_template_service import prompt_template_service
from services.call_recording_service import call_recording_service
from database.models import ConversationTurn, Call
from pydantic import BaseModel, Field
from twilio.rest import Client
from urllib.parse import quote
from services.agent_tools import execute_function
from services.intent_detection_service import intent_detection_service
import uuid

twilio_client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/twilio-elevenlabs", tags=["twilio-elevenlabs"])

# Active call context
call_context = {}


@router.post("/incoming-call")
async def handle_incoming_call_elevenlabs(request: Request):
    """Incoming call handler with ElevenLabs via WebSocket"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        global from_number_global
        global to_number_global

        from_number_global = from_number
        to_number_global = to_number

        company_id = request.query_params.get("company_id")
        agent_id = request.query_params.get("agent_id")
        
        logger.info(f"INCOMING CALL - CallSid: {call_sid}")
        logger.info(f"From: {from_number}, To: {to_number}")
        logger.info(f"Company: {company_id}, Agent: {agent_id}")
        
        # Generate TwiML response
        response = VoiceResponse()
        response.pause(length=1)
        
        # Connect to Media Stream
        connect = Connect()
        ws_domain = settings.base_url.replace('https://', '').replace('http://', '')

        call_context[call_sid] = {
            "company_id": company_id,
            "agent_id": agent_id,
            "from_number": from_number,
            "to_number": to_number
        }
        
        stream_url = f"wss://{ws_domain}/api/v1/twilio-elevenlabs/media-stream?call_sid={call_sid}"
        
        logger.info(f"Stream URL: {stream_url}")
        
        connect.stream(url=stream_url)
        response.append(connect)
        
        twiml_str = str(response)
        logger.info(f"TwiML Response:\n{twiml_str}")
        
        return Response(content=twiml_str, media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        response = VoiceResponse()
        response.say("An error occurred. Please try again.", voice="Polly.Joanna")
        return Response(content=str(response), media_type="application/xml")

@router.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Incoming call handler with proper echo prevention and interruption handling"""
    try:
        logger.info("WebSocket connection attempt...")
        await websocket.accept()
        logger.info("WebSocket ACCEPTED")
    except Exception as e:
        logger.error(f"Failed to accept WebSocket: {str(e)}")
        return
    
    conversation_transcript = []
    call_sid = websocket.query_params.get("call_sid")
    first_message_data = None
    is_agent_speaking = False
    stop_audio_flag = {'stop': False}
    current_audio_task = None
    current_processing_task = None
    greeting_sent = False
    greeting_start_time = None
    
    logger.info(f"Query params: {dict(websocket.query_params)}")
    
    if not call_sid:
        logger.info("No call_sid in query, waiting for Twilio 'start' event...")
        try:
            for attempt in range(3):
                message = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                data = json.loads(message)
                event_type = data.get("event")
                logger.info(f"Received event #{attempt + 1}: {event_type}")
                
                if event_type == "connected":
                    logger.info("Received 'connected' event")
                    continue
                elif event_type == "start":
                    call_sid = data.get("start", {}).get("callSid")
                    first_message_data = data
                    logger.info(f"Extracted call_sid: {call_sid}")
                    break
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for start event")
            await websocket.close(code=1008)
            return
        except Exception as e:
            logger.error(f"Error receiving messages: {str(e)}")
            await websocket.close(code=1011)
            return
    
    if not call_sid:
        logger.error("Could not obtain call_sid")
        await websocket.close(code=1008)
        return
    
    logger.info(f"Call SID validated: {call_sid}")
    
    # Get context from stored data
    context = call_context.get(call_sid, {})
    company_id = context.get("company_id")
    master_agent_id = context.get("agent_id")
    
    logger.info(f"Company ID: {company_id}, Master Agent: {master_agent_id}")
    
    master_agent = await agent_config_service.get_master_agent(company_id, master_agent_id)
    current_agent_context = master_agent
    
    call_metadata = {
        'start_time': datetime.utcnow(),
        'from_number': from_number_global,
        'to_number': to_number_global,
        'company_id': company_id
    }
    
    logger.info(f"Detected Company Id {company_id} for storing in Call Database")
    
    if not master_agent:
        logger.error(f"Master agent {master_agent_id} not found!")
        await websocket.close(code=1008, reason="Master agent not found")
        return
    
    logger.info(f"Master: {master_agent['name']}")
    
    available_agents = await agent_config_service.get_company_agents(company_id)
    specialized_agents = [
        a for a in available_agents
        if a['agent_id'] != master_agent_id
    ]
    
    logger.info(f"Specialized agents: {[a['name'] for a in specialized_agents]}")
    
    # Initialize services
    db = SessionLocal()
    deepgram_service = DeepgramWebSocketService()
    rag = get_rag_service()
    stream_sid = None
    
    call_state = {
        "first_interaction": True,
        "interaction_count": 0
    }
    
    try:
        async def on_deepgram_transcript(session_id: str, transcript: str):
            nonlocal conversation_transcript
            nonlocal is_agent_speaking
            nonlocal current_agent_context
            nonlocal current_audio_task
            nonlocal current_processing_task
            nonlocal stop_audio_flag
            nonlocal greeting_start_time
            
            if not transcript.strip():
                return
            
            if greeting_start_time:
                elapsed = (datetime.utcnow() - greeting_start_time).total_seconds()
                if elapsed < 3:
                    logger.debug(f"Ignoring early transcript (echo): '{transcript}' at {elapsed:.1f}s")
                    return
            
            logger.info(f"📝 Transcript: '{transcript}' | Agent speaking: {is_agent_speaking}")
            
            if is_agent_speaking:
                word_count = len(transcript.split())
                if word_count >= 3:
                    logger.warning(f"⚠️ REAL INTERRUPTION: '{transcript}'")
                    
                    # Set stop flag
                    stop_audio_flag['stop'] = True
                    
                    # Cancel BOTH audio playback AND processing tasks
                    if current_audio_task and not current_audio_task.done():
                        logger.info("Cancelling audio playback...")
                        current_audio_task.cancel()
                        try:
                            await current_audio_task
                        except asyncio.CancelledError:
                            pass
                    
                    if current_processing_task and not current_processing_task.done():
                        logger.info("Cancelling RAG processing...")
                        current_processing_task.cancel()
                        try:
                            await current_processing_task
                        except asyncio.CancelledError:
                            pass
                    
                    # Mark agent as not speaking
                    is_agent_speaking = False
                    
                    # Wait for buffer clear
                    logger.info("Waiting for Twilio to clear...")
                    await asyncio.sleep(1.0)
                    
                    # Reset flag
                    stop_audio_flag['stop'] = False
                    logger.info("✓ Ready for new input")
                else:
                    logger.debug(f"Ignoring short utterance: '{transcript}' ({word_count} words)")
                    return
            
            logger.info(f"USER SAID: '{transcript}'")
            
            # Save to transcript
            conversation_transcript.append({
                'role': 'user',
                'content': transcript,
                'timestamp': datetime.utcnow().isoformat()
            })
            
            # Sentiment analysis
            sentiment_analysis = prompt_template_service.detect_sentiment_and_urgency(
                transcript,
                current_agent_context
            )
            
            logger.info(f"Sentiment: {sentiment_analysis['sentiment']}, Urgency: {sentiment_analysis['urgency']}")
            
            if sentiment_analysis['urgency_keywords']:
                logger.warning(f"HIGH URGENCY DETECTED: {sentiment_analysis['urgency_keywords']}")
            
            # Save to DB
            try:
                db.add(ConversationTurn(
                    call_sid=call_sid,
                    role="user",
                    content=transcript,
                    created_at=datetime.utcnow()
                ))
                db.commit()
            except Exception as e:
                logger.error(f"DB error: {e}")
            
            # Handle urgent requests
            urgent_acknowledgment = None
            if sentiment_analysis['urgency'] == 'high' and sentiment_analysis['suggested_action']:
                urgent_acknowledgment = sentiment_analysis['suggested_action']
                logger.info(f"URGENT RESPONSE: '{urgent_acknowledgment}'")
                
                is_agent_speaking = True
                stop_audio_flag['stop'] = False
                
                current_audio_task = asyncio.create_task(
                    stream_elevenlabs_audio_with_playback(websocket, stream_sid, urgent_acknowledgment, stop_audio_flag)
                )
                
                try:
                    await current_audio_task
                except asyncio.CancelledError:
                    logger.info("Urgent acknowledgment cancelled")
                finally:
                    is_agent_speaking = False
                    current_audio_task = None
            
            # Agent routing
            current_agent_id = master_agent_id
            
            if specialized_agents:
                logger.info(f"Detecting intent for message #{call_state['interaction_count'] + 1}...")
                
                detected_agent = await intent_router_service.detect_intent(
                    transcript,
                    company_id,
                    master_agent,
                    specialized_agents
                )
                
                if detected_agent:
                    previous_agent_id = intent_router_service.get_current_agent(call_sid, master_agent_id)
                    intent_router_service.set_current_agent(call_sid, detected_agent)
                    current_agent_id = detected_agent
                    
                    agent_info = await agent_config_service.get_agent_by_id(detected_agent)
                    
                    if agent_info:
                        current_agent_context = agent_info
                        
                        if detected_agent != previous_agent_id and call_state["interaction_count"] > 0:
                            logger.info(f"RE-ROUTING to {agent_info['name']}")
                            
                            routing_message = f"Let me connect you with our {agent_info['name']}."
                            is_agent_speaking = True
                            stop_audio_flag['stop'] = False
                            
                            current_audio_task = asyncio.create_task(
                                stream_elevenlabs_audio_with_playback(websocket, stream_sid, routing_message, stop_audio_flag)
                            )
                            
                            await asyncio.sleep(0.5)
                            
                            try:
                                await current_audio_task
                            except asyncio.CancelledError:
                                logger.info("Routing message cancelled")
                            finally:
                                is_agent_speaking = False
                                current_audio_task = None
                        else:
                            logger.info(f"ROUTED to {agent_info['name']}")
                else:
                    logger.info(f"   Staying with MASTER")
            
            call_state["interaction_count"] += 1
            
            # Process and respond
            is_agent_speaking = True
            await asyncio.sleep(0.3)
            stop_audio_flag['stop'] = False
            
            current_processing_task = asyncio.create_task(
                process_and_respond_incoming(
                    transcript=transcript,
                    websocket=websocket,
                    stream_sid=stream_sid,
                    stop_audio_flag=stop_audio_flag,
                    db=db,
                    call_sid=call_sid,
                    current_agent_context=current_agent_context,
                    current_agent_id=current_agent_id,
                    company_id=company_id,
                    conversation_transcript=conversation_transcript,
                    sentiment_analysis=sentiment_analysis,
                    urgent_acknowledgment=urgent_acknowledgment,
                    call_metadata=call_metadata
                )
            )
            
            try:
                await current_processing_task
                logger.info("✓ Response completed")
            except asyncio.CancelledError:
                logger.info("Response cancelled by interruption")
            finally:
                is_agent_speaking = False
                current_audio_task = None
                current_processing_task = None
        
        # Initialize Deepgram
        session_id = f"deepgram_{call_sid}"
        logger.info(f"Initializing Deepgram...")
        
        deepgram_connected = await deepgram_service.initialize_session(
            session_id=session_id,
            callback=on_deepgram_transcript
        )
        
        if not deepgram_connected:
            logger.error(f"Deepgram connection failed")
            await websocket.close()
            return
        
        logger.info(f"Deepgram connected")
        
        if first_message_data and first_message_data.get("event") == "start":
            logger.info("Processing buffered start event...")
            stream_sid = first_message_data.get("streamSid")
            logger.info(f"STREAM STARTED: {stream_sid}")
            
            greeting = prompt_template_service.generate_greeting(master_agent)
            logger.info(f"Sending greeting: '{greeting}'")
            
            is_agent_speaking = True
            stop_audio_flag['stop'] = False
            greeting_start_time = datetime.utcnow()
            
            current_audio_task = asyncio.create_task(
                stream_elevenlabs_audio_with_playback(websocket, stream_sid, greeting, stop_audio_flag)
            )
            
            try:
                await current_audio_task
                logger.info("✓ Greeting completed - ready for customer response")
            except asyncio.CancelledError:
                logger.info("Greeting cancelled")
            finally:
                is_agent_speaking = False
                current_audio_task = None
            
            greeting_sent = True
            await asyncio.sleep(0.5)
        
        logger.info("Entering message loop...")
        
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                data = json.loads(message)
                event = data.get("event")
                
                if event == "connected":
                    logger.debug("Connected event")
                
                elif event == "start":
                    if not greeting_sent:
                        stream_sid = data.get("streamSid")
                        logger.info(f"STREAM STARTED: {stream_sid}")
                        
                        greeting = prompt_template_service.generate_greeting(master_agent)
                        logger.info(f"Sending greeting: '{greeting}'")
                        
                        is_agent_speaking = True
                        stop_audio_flag['stop'] = False
                        greeting_start_time = datetime.utcnow()
                        
                        current_audio_task = asyncio.create_task(
                            stream_elevenlabs_audio_with_playback(websocket, stream_sid, greeting, stop_audio_flag)
                        )
                        
                        try:
                            await current_audio_task
                            logger.info("Greeting completed")
                        except asyncio.CancelledError:
                            pass
                        finally:
                            is_agent_speaking = False
                            current_audio_task = None
                        
                        greeting_sent = True
                        await asyncio.sleep(0.5)
                
                elif event == "media":
                    # CRITICAL: Always process user audio for real-time interruption
                    payload = data.get("media", {}).get("payload")
                    if payload:
                        audio = await deepgram_service.convert_twilio_audio(payload, session_id)
                        if audio:
                            await deepgram_service.process_audio_chunk(session_id, audio)
                
                elif event == "stop":
                    logger.info(f"STREAM STOPPED")
                    break
            
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected")
                break
            except Exception as e:
                logger.error(f"Error: {str(e)}")
                break
    
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
    
    finally:
        logger.info(f"Cleaning up {call_sid}")
        logger.info(f"From: {call_metadata.get('from_number')}")
        logger.info(f"To: {call_metadata.get('to_number')}")
        logger.info(f"Company ID: {call_metadata.get('company_id')}")
        
        try:
            call_duration = 0
            if call_metadata.get('start_time'):
                call_duration = int((datetime.utcnow() - call_metadata['start_time']).total_seconds())
            
            logger.info(f"Uploading call data to S3...")
            logger.info(f"Duration: {call_duration}s")
            logger.info(f"Transcript turns: {len(conversation_transcript)}")
            
            s3_urls = await call_recording_service.save_call_data(
                call_sid=call_sid,
                company_id=company_id,
                agent_id=master_agent_id,
                transcript=conversation_transcript,
                recording_url=None,
                duration=call_duration,
                from_number=call_metadata.get('from_number'),
                to_number=call_metadata.get('to_number')
            )
            
            logger.info(f"S3 Upload Complete:")
            logger.info(f"Transcript: {s3_urls.get('transcript_url')}")
            
            try:
                call_record = db.query(Call).filter_by(call_sid=call_sid).first()
                
                if call_record:
                    call_record.transcription = s3_urls.get('transcript_url')
                    call_record.company_id = s3_urls.get('company_id')
                    call_record.from_number = s3_urls.get('from_number')
                    call_record.to_number = s3_urls.get('to_number')
                    call_record.duration = call_duration
                    call_record.status = 'completed'
                    call_record.ended_at = datetime.utcnow()
                    db.commit()
                    logger.info(f"Database updated with S3 URLs")
                else:
                    new_call = Call(
                        call_sid=call_sid,
                        company_id=call_metadata.get('company_id'),
                        from_number=call_metadata.get('from_number'),
                        to_number=call_metadata.get('to_number'),
                        status='completed',
                        duration=call_duration,
                        transcription=s3_urls.get('transcript_url'),
                        created_at=call_metadata['start_time'],
                        ended_at=datetime.utcnow()
                    )
                    db.add(new_call)
                    db.commit()
                    logger.info(f"Call record created")
            except Exception as db_error:
                logger.error(f"Database update error: {db_error}")
                db.rollback()
        
        except Exception as upload_error:
            logger.error(f"S3 upload error: {upload_error}")
            import traceback
            logger.error(traceback.format_exc())
        
        intent_router_service.clear_call(call_sid)
        
        try:
            await deepgram_service.close_session(session_id)
        except:
            pass
        
        call_context.pop(call_sid, None)
        db.close()
        
        logger.info(f"Cleanup complete")


async def stream_elevenlabs_audio_with_playback(websocket: WebSocket, stream_sid: str, text: str, stop_flag_ref: dict):
    """Stream audio AND simulate playback in one task for proper cancellation"""
    if not stream_sid:
        logger.error("No stream_sid")
        return
    
    try:
        logger.info(f"🔊 Generating audio: '{text[:50]}...'")
        chunk_count = 0
        
        # Stream audio chunks
        async for audio_chunk in elevenlabs_service.generate(text):
            if stop_flag_ref.get('stop', False):
                logger.info(f"🛑 Audio interrupted after {chunk_count} chunks")
                try:
                    clear_message = {"event": "clear", "streamSid": stream_sid}
                    await websocket.send_json(clear_message)
                    logger.info("✓ Sent clear command")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"Clear error: {e}")
                raise asyncio.CancelledError()
            
            if audio_chunk and stream_sid:
                message = {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": audio_chunk}
                }
                await websocket.send_json(message)
                chunk_count += 1
        
        logger.info(f"✓ Sent {chunk_count} chunks to Twilio")
        
        # Calculate playback duration (each chunk ~20ms + buffer)
        playback_duration = (chunk_count * 0.02) + 0.5
        logger.info(f"⏳ Simulating playback: {playback_duration:.1f}s")
        
        # Wait for playback while checking for interruption
        elapsed = 0
        check_interval = 0.1
        
        while elapsed < playback_duration:
            if stop_flag_ref.get('stop', False):
                logger.info(f"🛑 Playback interrupted at {elapsed:.1f}s")
                try:
                    clear_message = {"event": "clear", "streamSid": stream_sid}
                    await websocket.send_json(clear_message)
                    await asyncio.sleep(0.5)
                except:
                    pass
                raise asyncio.CancelledError()
            
            await asyncio.sleep(check_interval)
            elapsed += check_interval
        
        logger.info(f"✓ Playback completed: {playback_duration:.1f}s")
        
    except asyncio.CancelledError:
        logger.info(f"🛑 Audio+playback cancelled")
        try:
            clear_message = {"event": "clear", "streamSid": stream_sid}
            await websocket.send_json(clear_message)
            await asyncio.sleep(0.5)
        except:
            pass
        raise
    except Exception as e:
        logger.error(f"Error streaming audio: {e}")


async def process_and_respond_incoming(
    transcript: str,
    websocket: WebSocket,
    stream_sid: str,
    stop_audio_flag: dict,
    db: Session,
    call_sid: str,
    current_agent_context: dict,
    current_agent_id: str,
    company_id: str,
    conversation_transcript: list,
    sentiment_analysis: dict,
    urgent_acknowledgment: str = None,
    call_metadata: dict = None
):
    """Process incoming call with AI-powered intelligent routing"""
    
    rag = get_rag_service()
    
    try:
        # AI-POWERED DECISION: Should we retrieve documents?
        routing_decision = await rag_routing_service.should_retrieve_documents(
            user_message=transcript,
            conversation_history=conversation_transcript,
            call_type="incoming",
            agent_context=current_agent_context
        )
        
        response_strategy = routing_decision['response_strategy']
        
        logger.info(f"🎯 AI Routing: {response_strategy}")
        logger.info(f"   Reasoning: {routing_decision['reasoning']}")
        
        # Build conversation context
        conversation_messages = []
        for msg in conversation_transcript[-10:]:
            if msg['role'] in ['user', 'assistant']:
                conversation_messages.append({
                    'role': msg['role'],
                    'content': msg['content']
                })
        
        # Strategy 1: Direct canned response (no API calls)
        if response_strategy == 'direct_canned':
            logger.info(f"✅ Using canned response (0 API calls)")
            
            # Use a simple LLM call to generate natural response
            simple_prompt = [
                {"role": "system", "content": f"You are a helpful assistant. Respond naturally to this greeting/farewell in 1 sentence."},
                {"role": "user", "content": transcript}
            ]
            
            response_chunks = []
            async for chunk in rag.llm.astream(simple_prompt):
                if chunk.content:
                    response_chunks.append(chunk.content)
            
            llm_response = "".join(response_chunks)
            
            conversation_transcript.append({
                'role': 'assistant',
                'content': llm_response,
                'timestamp': datetime.utcnow().isoformat(),
                'strategy': 'direct_canned',
                'documents_retrieved': False
            })
            
            db.add(ConversationTurn(
                call_sid=call_sid,
                role="assistant",
                content=llm_response,
                created_at=datetime.utcnow()
            ))
            db.commit()
            
            await stream_elevenlabs_audio_with_playback(
                websocket, stream_sid, llm_response, stop_audio_flag
            )
            return
        
        # Strategy 2: Use conversation context only (LLM without document retrieval)
        elif response_strategy == 'conversation_context':
            logger.info(f"💬 Using conversation context only (1 LLM call, no vector search)")
            
            support_context = f"""[INCOMING SUPPORT CALL - ACTIVE CONVERSATION]
Customer's Sentiment: {sentiment_analysis.get('sentiment', 'neutral')}
Urgency: {sentiment_analysis.get('urgency', 'normal')}
Customer's Current Message: "{transcript}"

You are continuing an active conversation. Use the conversation history to respond naturally.

**GUIDELINES:**
- Reference what was already discussed
- Keep responses natural and conversational (2-4 sentences typical)
- If customer asks about something truly NEW that you don't know, say: "Let me look that up in our documentation"
- Use create_ticket function if they report an issue
- Adapt response length based on what customer asks for

**IMPORTANT:**
- This is a PHONE conversation
- You can see the conversation history below
- Don't repeat information already provided
- Be helpful and empathetic"""
            
            conversation_messages.insert(0, {
                'role': 'system',
                'content': support_context
            })
            
            # Direct LLM with function calling (no RAG)
            response = await rag.llm_with_functions.ainvoke(conversation_messages)
            
            # Check for function call
            if hasattr(response, 'additional_kwargs') and 'function_call' in response.additional_kwargs:
                function_call = response.additional_kwargs['function_call']
                function_name = function_call['name']
                arguments = json.loads(function_call['arguments'])
                
                logger.info(f"Function call: {function_name}")
                
                from services.agent_tools import execute_function
                llm_response = await execute_function(
                    function_name=function_name,
                    arguments=arguments,
                    company_id=company_id,
                    call_sid=call_sid or "unknown",
                    campaign_id=None,
                user_timezone=call_metadata.get('user_timezone', 'UTC'),  # NEW
                business_hours={'start': '09:00', 'end': '18:00'}
                )
            else:
                llm_response = response.content
            
            conversation_transcript.append({
                'role': 'assistant',
                'content': llm_response,
                'timestamp': datetime.utcnow().isoformat(),
                'strategy': 'conversation_context',
                'documents_retrieved': False
            })
        
        # Strategy 3: Full RAG with document retrieval
        elif response_strategy == 'document_retrieval':
            logger.info(f"📚 Using FULL RAG (document retrieval + LLM)")
            
            support_context = f"""[INCOMING SUPPORT CALL]
Customer's Sentiment: {sentiment_analysis.get('sentiment', 'neutral')}
Urgency: {sentiment_analysis.get('urgency', 'normal')}
Customer's Current Message: "{transcript}"

You are on a LIVE SUPPORT PHONE CALL. Use the provided documentation to give accurate answers.

**RESPONSE GUIDELINES:**
- If customer asks for details, provide them completely
- If customer says "tell me more", expand with specifics
- Match the customer's urgency and tone
- Use create_ticket function for issues requiring follow-up
- Be solution-focused and clear

**IMPORTANT:**
- Use the documentation provided to answer accurately
- If documentation doesn't cover it, be honest
- This is a phone conversation - be conversational"""
            
            conversation_messages.insert(0, {
                'role': 'system',
                'content': support_context
            })
            
            if sentiment_analysis['urgency'] == 'high':
                conversation_messages.insert(0, {
                    'role': 'system',
                    'content': f"""[URGENT REQUEST]
Keywords: {', '.join(sentiment_analysis.get('urgency_keywords', []))}
Priority: HIGH - Address immediately"""
                })
            
            # Full RAG pipeline
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
            
            # Handle urgent fallback if needed
            if sentiment_analysis['urgency'] == 'high' and len(llm_response) < 50:
                logger.warning(f"No RAG solution for urgent request - creating ticket")
                from services.agent_tools import execute_function
                ticket_result = await execute_function(
                    function_name="create_ticket",
                    arguments={
                        "title": f"Urgent: {transcript[:50]}",
                        "description": f"High urgency: {transcript}",
                        "customer_phone": call_metadata.get('from_number'),
                        "priority": "high"
                    },
                    company_id=company_id,
                    call_sid=call_sid
                )
                llm_response = f"{llm_response} {ticket_result}"
            
            conversation_transcript.append({
                'role': 'assistant',
                'content': llm_response,
                'timestamp': datetime.utcnow().isoformat(),
                'strategy': 'document_retrieval',
                'documents_retrieved': True
            })
        
        else:
            # Fallback
            logger.warning(f"Unknown strategy: {response_strategy}, using conversation context")
            llm_response = "I'm here to help. Could you tell me more about what you need?"
            
            conversation_transcript.append({
                'role': 'assistant',
                'content': llm_response,
                'timestamp': datetime.utcnow().isoformat(),
                'strategy': 'fallback',
                'documents_retrieved': False
            })
        
        logger.info(f"AGENT: '{llm_response[:100]}...'")
        
        # Save to DB
        db.add(ConversationTurn(
            call_sid=call_sid,
            role="assistant",
            content=llm_response,
            created_at=datetime.utcnow()
        ))
        db.commit()
        
        # Stream response
        await stream_elevenlabs_audio_with_playback(
            websocket, stream_sid, llm_response, stop_audio_flag
        )
        
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        error_msg = "I'm having trouble. Could you please repeat?"
        await stream_elevenlabs_audio_with_playback(
            websocket, stream_sid, error_msg, stop_audio_flag
        )

async def process_and_respond_outbound(
    transcript: str,
    websocket: WebSocket,
    stream_sid: str,
    stop_audio_flag: dict,
    db: Session,
    call_sid: str,
    current_agent_context: dict,
    current_agent_id: str,
    company_id: str,
    conversation_transcript: list,
    intent_analysis: dict,
    call_type: str
):
    """
    CORRECT FLOW:
    - We HAVE: name, phone (from curl)
    - We NEED: date, time, email
    """
    
    rag = get_rag_service()
    
    try:
        from services.datetime_parser_service import datetime_parser_service
        from services.booking_orchestration_service import booking_orchestrator, BookingState
        from datetime import timedelta
        
        # Get call metadata
        call_metadata = call_context.get(call_sid, {})
        campaign_id = call_metadata.get('campaign_id')
        customer_name = call_metadata.get('customer_name', 'Customer')
        customer_phone = call_metadata.get('to_number')
        
        # Check booking session
        booking_session = booking_orchestrator.get_session(call_sid)
        is_booking_mode = booking_session and booking_orchestrator.is_booking_active(call_sid)
        
        # Detect booking intent
        buying_readiness = intent_analysis.get('buying_readiness', 0)
        intent_type = intent_analysis.get('intent_type')
        
        should_start_booking = (
            buying_readiness >= 70 or
            intent_type == 'strong_buying' or
            any(word in transcript.lower() for word in ['book', 'schedule', 'appointment', 'yes', 'sure'])
        )
        
        # Initialize booking (name + phone already known)
        if should_start_booking and not booking_session:
            booking_session = booking_orchestrator.initialize_booking(
                call_sid=call_sid,
                customer_name=customer_name,    # ✅ From curl
                customer_phone=customer_phone,  # ✅ From curl
                campaign_id=campaign_id
            )
            is_booking_mode = True
            booking_orchestrator.transition_state(call_sid, BookingState.COLLECTING_DATE, "Customer interested")
            logger.info(f"🎫 Booking started for {customer_name}")
        
        is_sales_call = is_booking_mode or buying_readiness >= 50
        
        # AI-powered datetime parsing
        datetime_info = await datetime_parser_service.parse_user_datetime(
            user_input=transcript,
            user_timezone=call_metadata.get('user_timezone', 'UTC'),
            business_hours={'start': '09:00', 'end': '18:00'}
        )
        
        # Update booking session if datetime found
        if datetime_info.get('parsed_successfully') and booking_session:
            logger.info(f"✓ AI extracted: {datetime_info.get('user_friendly')}")
            logger.info(f"  Reasoning: {datetime_info.get('ai_reasoning')}")
            
            if datetime_info.get('date'):
                booking_orchestrator.update_session_data(call_sid, 'date', datetime_info['date'])
            if datetime_info.get('time'):
                booking_orchestrator.update_session_data(call_sid, 'time', datetime_info['time'])
            if datetime_info.get('datetime_iso'):
                booking_orchestrator.update_session_data(call_sid, 'datetime_iso', datetime_info['datetime_iso'])
        
        # Check if transcript contains an email
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        import re
        email_match = re.search(email_pattern, transcript)
        if email_match and booking_session:
            email = email_match.group(0)
            booking_orchestrator.update_session_data(call_sid, 'email', email)
            logger.info(f"📧 Extracted email: {email}")
        
        # Build minimal conversation context
        conversation_messages = []
        for msg in conversation_transcript[-6:]:
            if msg['role'] in ['user', 'assistant']:
                conversation_messages.append({
                    'role': msg['role'],
                    'content': msg['content']
                })
        
        # Get context
        company_name = current_agent_context.get('name', 'our company')
        
        now = datetime.now()
        tomorrow = now + timedelta(days=1)
        day_after = now + timedelta(days=2)
        
        # Choose prompt
        if is_sales_call:
            # BOOKING MODE
            next_action = booking_orchestrator.get_next_action(call_sid) if booking_session else {'prompt_hint': 'Ask for date/time'}
            collected = booking_session['collected_data'] if booking_session else {}
            customer = booking_session['customer_info'] if booking_session else {}
            
            has_date = collected.get('date') is not None
            has_time = collected.get('time') is not None
            has_email = customer.get('email') is not None
            
            prompt = f"""Booking demo for {customer_name}.

**We have:**
- Name: ✅ {customer_name}
- Phone: ✅ {customer_phone}
- Date: {'✅ ' + collected.get('date') if has_date else '❌ NEED'}
- Time: {'✅ ' + collected.get('time') if has_time else '❌ NEED'}
- Email: {'✅ ' + customer.get('email') if has_email else '❌ NEED'}

**Today:** {now.strftime('%A, %B %d')}

**Next step:** {next_action['prompt_hint']}

**CRITICAL RULES:**
1. We already have name and phone - DO NOT ask for them
2. We NEED to collect: date, time, and email
3. Flow: date → time → check availability → email → confirm
4. NO support tickets in sales calls
5. Max 1 sentence responses

**If date+time collected but no email:**
Say: "Perfect! What's your email for the confirmation?"

**Suggest times:**
- "Tomorrow at 10 AM?"
- "{tomorrow.strftime('%B %d')} at 2 PM?"

**After email collected:**
Use verify_customer_email function, then create_booking

Be brief. Book it."""

        else:
            # SALES MODE
            prompt = f"""Sales for {company_name}.

Customer: {customer_name}
Interest: {buying_readiness}%

**Approach:**
- 70%+: "Want to book a demo?"
- 40-70%: Share 1 benefit
- <40%: Ask 1 question

1 sentence max.
NO tickets."""
        
        conversation_messages.insert(0, {'role': 'system', 'content': prompt})
        
        # Single API call
        logger.info("💬 LLM call")
        
        response = await rag.llm_with_functions.ainvoke(conversation_messages)
        
        # Handle function calls
        if hasattr(response, 'additional_kwargs') and 'function_call' in response.additional_kwargs:
            function_call = response.additional_kwargs['function_call']
            function_name = function_call['name']
            arguments = json.loads(function_call['arguments'])
            
            logger.info(f"📞 Function: {function_name}")
            
            # Block ticket creation during sales
            if function_name == 'create_ticket' and is_sales_call:
                logger.warning(f"🚫 BLOCKED: create_ticket during sales")
                llm_response = "Let me book that demo. What date works?"
            
            else:
                # Execute function
                logger.info(f"✅ Executing: {function_name}")
                
                # Update state before execution
                if function_name == 'check_slot_availability' and booking_session:
                    booking_orchestrator.transition_state(call_sid, BookingState.CHECKING_AVAILABILITY, "Checking")
                
                from services.agent_tools import execute_function
                llm_response = await execute_function(
                    function_name=function_name,
                    arguments=arguments,
                    company_id=company_id,
                    call_sid=call_sid,
                    campaign_id=campaign_id,
                    user_timezone=call_metadata.get('user_timezone', 'UTC'),
                    business_hours={'start': '09:00', 'end': '18:00'}
                )
                
                # Update state after execution
                if function_name == 'check_slot_availability':
                    if 'available' in llm_response.lower():
                        booking_orchestrator.update_session_data(call_sid, 'slot_available', True)
                        # ✅ After slot confirmed, ask for email
                        booking_orchestrator.transition_state(call_sid, BookingState.COLLECTING_EMAIL, "Slot available, need email")
                    else:
                        logger.info("Slot unavailable, staying in collecting state")
                
                elif function_name == 'verify_customer_email':
                    booking_orchestrator.update_session_data(call_sid, 'email_verified', True)
                    booking_orchestrator.transition_state(call_sid, BookingState.CONFIRMING_BOOKING, "Email verified")
                
                elif function_name == 'create_booking':
                    booking_orchestrator.transition_state(call_sid, BookingState.COMPLETED, "Booking complete!")
                    logger.info(f"✅ BOOKING COMPLETE: {customer_name}")
        
        else:
            # No function call - direct response
            llm_response = response.content
        
        # Save to transcript
        conversation_transcript.append({
            'role': 'assistant',
            'content': llm_response,
            'timestamp': datetime.utcnow().isoformat(),
            'booking_mode': is_booking_mode
        })
        
        logger.info(f"AGENT: {llm_response[:80]}{'...' if len(llm_response) > 80 else ''}")
        
        # Save to DB
        db.add(ConversationTurn(
            call_sid=call_sid,
            role="assistant",
            content=llm_response,
            created_at=datetime.utcnow()
        ))
        db.commit()
        
        # Stream to customer
        await stream_elevenlabs_audio_with_playback(
            websocket, stream_sid, llm_response, stop_audio_flag
        )
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        error_msg = "Could you repeat that?"
        await stream_elevenlabs_audio_with_playback(
            websocket, stream_sid, error_msg, stop_audio_flag
        )

@router.post("/outbound-connect")
async def handle_outbound_connect(request: Request):
    """Outbound call handler with ElevenLabs via WebSocket"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        # Store in global variables
        global from_number_global
        global to_number_global
        
        from_number_global = from_number
        to_number_global = to_number
        
        # Get parameters
        campaign_id = request.query_params.get("campaign_id")
        company_id = request.query_params.get("company_id")
        agent_id = request.query_params.get("agent_id")
        customer_name = request.query_params.get("customer_name", "")
        
        logger.info(f"OUTBOUND CALL - CallSid: {call_sid}")
        logger.info(f"From: {from_number}, To: {to_number}")
        logger.info(f"Company: {company_id}, Agent: {agent_id}")
        logger.info(f"Customer: {customer_name}, Campaign: {campaign_id}")
        
        # Generate TwiML response
        response = VoiceResponse()
        
        # Connect to Media Stream
        connect = Connect()
        ws_domain = settings.base_url.replace('https://', '').replace('http://', '')
        
        # Store context
        call_context[call_sid] = {
            "company_id": company_id,
            "agent_id": agent_id,
            "from_number": from_number,
            "to_number": to_number,
            "customer_name": customer_name,
            "campaign_id": campaign_id,
            "call_type": "outgoing"
        }
        
        stream_url = f"wss://{ws_domain}/api/v1/twilio-elevenlabs/outbound-stream?call_sid={call_sid}"
        
        logger.info(f"Stream URL: {stream_url}")
        
        connect.stream(url=stream_url)
        response.append(connect)
        
        twiml_str = str(response)
        logger.info(f"TwiML Response:\n{twiml_str}")
        
        return Response(content=twiml_str, media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        response = VoiceResponse()
        response.say("An error occurred. Please try again.", voice="Polly.Joanna")
        return Response(content=str(response), media_type="application/xml")

@router.websocket("/outbound-stream")
async def handle_outbound_stream(websocket: WebSocket):
    """Outbound call streaming with proper echo prevention and interruption handling"""
    try:
        logger.info("WebSocket connection attempt...")
        await websocket.accept()
        logger.info("WebSocket ACCEPTED")
    except Exception as e:
        logger.error(f"Failed to accept WebSocket: {str(e)}")
        return
    
    conversation_transcript = []
    call_sid = websocket.query_params.get("call_sid")
    first_message_data = None
    is_agent_speaking = False
    stop_audio_flag = {'stop': False}
    current_audio_task = None
    current_processing_task = None
    greeting_sent = False
    greeting_start_time = None
    rejection_count = 0  # Track rejections
    
    logger.info(f"Query params: {dict(websocket.query_params)}")
    
    if not call_sid:
        logger.info("No call_sid in query, waiting for Twilio 'start' event...")
        try:
            for attempt in range(3):
                message = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                data = json.loads(message)
                event_type = data.get("event")
                
                logger.info(f"Received event #{attempt + 1}: {event_type}")
                
                if event_type == "connected":
                    logger.info("Received 'connected' event")
                    continue
                elif event_type == "start":
                    call_sid = data.get("start", {}).get("callSid")
                    first_message_data = data
                    logger.info(f"Extracted call_sid: {call_sid}")
                    break
        
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for start event")
            await websocket.close(code=1008)
            return
        except Exception as e:
            logger.error(f"Error receiving messages: {str(e)}")
            await websocket.close(code=1011)
            return
    
    if not call_sid:
        logger.error("Could not obtain call_sid")
        await websocket.close(code=1008)
        return
    
    logger.info(f"Call SID validated: {call_sid}")
    
    # Get context from stored data
    context = call_context.get(call_sid, {})
    company_id = context.get("company_id")
    master_agent_id = context.get("agent_id")
    customer_name = context.get("customer_name", "")
    campaign_id = context.get("campaign_id", "")
    call_type = context.get("call_type", "outgoing")
    
    logger.info(f"Company ID: {company_id}, Master Agent: {master_agent_id}")
    logger.info(f"Customer: {customer_name}, Campaign: {campaign_id}")
    
    # Fetch master agent config
    master_agent = await agent_config_service.get_master_agent(company_id, master_agent_id)
    current_agent_context = master_agent
    
    call_metadata = {
        'start_time': datetime.utcnow(),
        'from_number': from_number_global,
        'to_number': to_number_global,
        'company_id': company_id,
        'call_type': call_type
    }
    
    logger.info(f"Detected Company Id {company_id} for storing in Call Database")
    logger.info(f"Call Type: {call_type}")
    
    if not master_agent:
        logger.error(f"Master agent {master_agent_id} not found!")
        await websocket.close(code=1008, reason="Master agent not found")
        return
    
    logger.info(f"Master: {master_agent['name']}")

    additional_context = master_agent.get('additional_context', {})
    business_context = additional_context.get('businessContext', 'our services')

    company_name = "our company"
    
    greeting = (
        f"Hello {customer_name}! This is calling from {company_name}. "
        f"I'm reaching out because we offer {business_context}. "
        f"Would you be interested in learning more about this?"
    )
    
    logger.info(f"Greeting pre-generated: '{greeting}'")
    
    # Load specialized agents
    available_agents = await agent_config_service.get_company_agents(company_id)
    specialized_agents = [
        a for a in available_agents
        if a['agent_id'] != master_agent_id
    ]
    
    logger.info(f"Master: {master_agent['name']}")
    logger.info(f"Specialized agents: {[a['name'] for a in specialized_agents]}")
    
    # Initialize services
    db = SessionLocal()
    deepgram_service = DeepgramWebSocketService()
    rag = get_rag_service()
    stream_sid = None
    
    call_state = {
        "first_interaction": True,
        "interaction_count": 0
    }
    
    try:
        async def on_deepgram_transcript(session_id: str, transcript: str):
            nonlocal conversation_transcript
            nonlocal is_agent_speaking
            nonlocal current_agent_context
            nonlocal current_audio_task
            nonlocal current_processing_task
            nonlocal stop_audio_flag
            nonlocal greeting_start_time
            nonlocal rejection_count
            
            if not transcript.strip():
                return
            
            if greeting_start_time:
                elapsed = (datetime.utcnow() - greeting_start_time).total_seconds()
                if elapsed < 3:
                    logger.debug(f"Ignoring early transcript (echo): '{transcript}' at {elapsed:.1f}s")
                    return
            
            logger.info(f"Transcript: '{transcript}' | Agent speaking: {is_agent_speaking}")
            
            if is_agent_speaking:
                word_count = len(transcript.split())
                if word_count >= 3:
                    logger.warning(f"REAL INTERRUPTION: '{transcript}'")

                    stop_audio_flag['stop'] = True

                    if current_audio_task and not current_audio_task.done():
                        logger.info("Cancelling audio playback...")
                        current_audio_task.cancel()
                        try:
                            await current_audio_task
                        except asyncio.CancelledError:
                            pass
                    
                    if current_processing_task and not current_processing_task.done():
                        logger.info("Cancelling RAG processing...")
                        current_processing_task.cancel()
                        try:
                            await current_processing_task
                        except asyncio.CancelledError:
                            pass
                    
                    # Mark agent as not speaking
                    is_agent_speaking = False
                    
                    # Wait for buffer clear
                    logger.info("Waiting for Twilio to clear...")
                    await asyncio.sleep(1.0)
                    
                    # Reset flag
                    stop_audio_flag['stop'] = False
                    logger.info("✓ Ready for new input")
                else:
                    logger.debug(f"Ignoring short utterance: '{transcript}' ({word_count} words)")
                    return
            
            logger.info(f"CUSTOMER SAID: '{transcript}'")
            
            # Detect customer intent for outbound calls
            intent_analysis = await intent_detection_service.detect_customer_intent(
                customer_message=transcript,
                conversation_history=conversation_transcript,
                call_type=call_type
            )
            
            # Save to transcript
            conversation_transcript.append({
                'role': 'user',
                'content': transcript,
                'timestamp': datetime.utcnow().isoformat(),
                'intent_type': intent_analysis.get('intent_type'),
                'sentiment': intent_analysis.get('sentiment'),
                'buying_readiness': intent_analysis.get('buying_readiness')
            })
            
            # Save to DB
            try:
                db.add(ConversationTurn(
                    call_sid=call_sid,
                    role="user",
                    content=transcript,
                    created_at=datetime.utcnow()
                ))
                db.commit()
            except Exception as e:
                logger.error(f"DB error: {e}")
            
            intent_type = intent_analysis.get('intent_type')
            buying_readiness = intent_analysis.get('buying_readiness', 0)
            should_book = intent_analysis.get('should_book', False)
            should_persuade = intent_analysis.get('should_persuade', True)
            should_end_call = intent_analysis.get('should_end_call', False)
            
            logger.info(f"PERSUASION MODE (Intent: {intent_type}, Readiness: {buying_readiness}%)")

            if intent_type == 'rejection':
                rejection_count += 1
                logger.warning(f"Rejection #{rejection_count} detected")
                
                if rejection_count >= 2:
                    logger.info(f"2 REJECTIONS DETECTED - Ending call gracefully")
                    
                    farewell_message = (
                        f"I completely understand, {customer_name}. "
                        f"Thank you for your time today. If you change your mind or have any questions in the future, "
                        f"please don't hesitate to reach out. Have a wonderful day!"
                    )
                    
                    conversation_transcript.append({
                        'role': 'assistant',
                        'content': farewell_message,
                        'timestamp': datetime.utcnow().isoformat(),
                        'call_ended': True
                    })
                    
                    try:
                        db.add(ConversationTurn(
                            call_sid=call_sid,
                            role="assistant",
                            content=farewell_message,
                            created_at=datetime.utcnow()
                        ))
                        db.commit()
                    except Exception as e:
                        logger.error(f"DB error: {e}")
                    
                    is_agent_speaking = True
                    stop_audio_flag['stop'] = False
                    
                    current_audio_task = asyncio.create_task(
                        stream_elevenlabs_audio_with_playback(websocket, stream_sid, farewell_message, stop_audio_flag)
                    )
                    
                    try:
                        await current_audio_task
                    except asyncio.CancelledError:
                        logger.info("Farewell message cancelled")
                    finally:
                        is_agent_speaking = False
                        current_audio_task = None
                    
                    # End the call
                    logger.info("Ending call after 2 rejections")
                    return
            else:
                # Reset rejection count if customer shows interest
                if intent_type in ['soft_interest', 'strong_buying', 'question']:
                    rejection_count = 0
                    logger.info("Customer engaged - reset rejection count")

            current_agent_id = master_agent_id
            
            if specialized_agents:
                logger.info(f"Detecting intent for message #{call_state['interaction_count'] + 1}...")
                
                detected_agent = await intent_router_service.detect_intent(
                    transcript,
                    company_id,
                    master_agent,
                    specialized_agents
                )
                
                if detected_agent:
                    previous_agent_id = intent_router_service.get_current_agent(call_sid, master_agent_id)
                    intent_router_service.set_current_agent(call_sid, detected_agent)
                    current_agent_id = detected_agent
                    
                    agent_info = await agent_config_service.get_agent_by_id(detected_agent)
                    if agent_info:
                        current_agent_context = agent_info
                        
                        if detected_agent != previous_agent_id and call_state["interaction_count"] > 0:
                            logger.info(f"RE-ROUTING to {agent_info['name']}")
                            
                            routing_message = f"Let me connect you with our {agent_info['name']}."
                            
                            is_agent_speaking = True
                            stop_audio_flag['stop'] = False
                            
                            current_audio_task = asyncio.create_task(
                                stream_elevenlabs_audio_with_playback(websocket, stream_sid, routing_message, stop_audio_flag)
                            )
                            
                            await asyncio.sleep(0.5)
                            
                            try:
                                await current_audio_task
                            except asyncio.CancelledError:
                                logger.info("Routing message cancelled")
                            finally:
                                is_agent_speaking = False
                                current_audio_task = None
                        else:
                            logger.info(f"ROUTED to {agent_info['name']}")
                else:
                    logger.info(f"   Staying with MASTER")
            
            call_state["interaction_count"] += 1

            is_agent_speaking = True
            await asyncio.sleep(0.3)
            stop_audio_flag['stop'] = False
            
            current_processing_task = asyncio.create_task(
                process_and_respond_outbound(
                    transcript=transcript,
                    websocket=websocket,
                    stream_sid=stream_sid,
                    stop_audio_flag=stop_audio_flag,
                    db=db,
                    call_sid=call_sid,
                    current_agent_context=current_agent_context,
                    current_agent_id=current_agent_id,
                    company_id=company_id,
                    conversation_transcript=conversation_transcript,
                    intent_analysis=intent_analysis,
                    call_type=call_type
                )
            )
            
            try:
                await current_processing_task
                logger.info("✓ Response completed")
            except asyncio.CancelledError:
                logger.info("Response cancelled by interruption")
            finally:
                is_agent_speaking = False
                current_audio_task = None
                current_processing_task = None

        session_id = f"deepgram_{call_sid}"
        logger.info(f"Initializing Deepgram...")
        
        deepgram_start = datetime.utcnow()
        deepgram_connected = await deepgram_service.initialize_session(
            session_id=session_id,
            callback=on_deepgram_transcript
        )
        deepgram_duration = (datetime.utcnow() - deepgram_start).total_seconds()
        
        if not deepgram_connected:
            logger.error(f"Deepgram connection failed")
            await websocket.close()
            return
        
        logger.info(f"Deepgram connected in {deepgram_duration:.2f}s")

        if first_message_data and first_message_data.get("event") == "start":
            stream_sid = first_message_data.get("streamSid")
            logger.info(f"STREAM STARTED: {stream_sid}")

            logger.info(f"Sending greeting immediately...")
            
            conversation_transcript.append({
                'role': 'assistant',
                'content': greeting,
                'timestamp': datetime.utcnow().isoformat()
            })
            
            is_agent_speaking = True
            stop_audio_flag['stop'] = False
            greeting_start_time = datetime.utcnow()
            
            greeting_audio_start = datetime.utcnow()
            
            current_audio_task = asyncio.create_task(
                stream_elevenlabs_audio_with_playback(websocket, stream_sid, greeting, stop_audio_flag)
            )
            
            try:
                await current_audio_task
                greeting_total_duration = (datetime.utcnow() - greeting_audio_start).total_seconds()
                logger.info(f"Greeting completed in {greeting_total_duration:.2f}s - ready for customer response")
            except asyncio.CancelledError:
                logger.info("Greeting cancelled")
            finally:
                is_agent_speaking = False
                current_audio_task = None
            
            greeting_sent = True
            await asyncio.sleep(0.5)
        
        logger.info("Entering message loop...")
        
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                data = json.loads(message)
                event = data.get("event")
                
                if event == "connected":
                    logger.debug("Connected event")
                
                elif event == "start":
                    if not greeting_sent:
                        stream_sid = data.get("streamSid")
                        logger.info(f"STREAM STARTED (delayed): {stream_sid}")

                        logger.info(f"Sending greeting...")
                        
                        conversation_transcript.append({
                            'role': 'assistant',
                            'content': greeting,
                            'timestamp': datetime.utcnow().isoformat()
                        })
                        
                        is_agent_speaking = True
                        stop_audio_flag['stop'] = False
                        greeting_start_time = datetime.utcnow()
                        
                        current_audio_task = asyncio.create_task(
                            stream_elevenlabs_audio_with_playback(websocket, stream_sid, greeting, stop_audio_flag)
                        )
                        
                        try:
                            await current_audio_task
                            logger.info("Greeting completed")
                        except asyncio.CancelledError:
                            pass
                        finally:
                            is_agent_speaking = False
                            current_audio_task = None
                        
                        greeting_sent = True
                        await asyncio.sleep(0.5)
                
                elif event == "media":
                    # Process user audio for interruption detection
                    payload = data.get("media", {}).get("payload")
                    if payload:
                        audio = await deepgram_service.convert_twilio_audio(payload, session_id)
                        if audio:
                            await deepgram_service.process_audio_chunk(session_id, audio)
                
                elif event == "stop":
                    logger.info(f"STREAM STOPPED")
                    break
            
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected")
                break
            except Exception as e:
                logger.error(f"Error: {str(e)}")
                break
    
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
    
    finally:
        logger.info(f"Cleaning up {call_sid}")
        logger.info(f"From: {call_metadata.get('from_number')}")
        logger.info(f"To: {call_metadata.get('to_number')}")
        logger.info(f"Company ID: {call_metadata.get('company_id')}")
        
        try:
            call_duration = 0
            if call_metadata.get('start_time'):
                call_duration = int((datetime.utcnow() - call_metadata['start_time']).total_seconds())
            
            logger.info(f"Uploading call data to S3...")
            logger.info(f"Duration: {call_duration}s")
            logger.info(f"Transcript turns: {len(conversation_transcript)}")
            
            s3_urls = await call_recording_service.save_call_data(
                call_sid=call_sid,
                company_id=company_id,
                agent_id=master_agent_id,
                transcript=conversation_transcript,
                recording_url=None,
                duration=call_duration,
                from_number=call_metadata.get('from_number'),
                to_number=call_metadata.get('to_number')
            )
            
            logger.info(f"S3 Upload Complete:")
            logger.info(f"Transcript: {s3_urls.get('transcript_url')}")
            
            try:
                call_record = db.query(Call).filter_by(call_sid=call_sid).first()
                
                if call_record:
                    call_record.transcription = s3_urls.get('transcript_url')
                    call_record.company_id = s3_urls.get('company_id')
                    call_record.from_number = s3_urls.get('from_number')
                    call_record.to_number = s3_urls.get('to_number')
                    call_record.duration = call_duration
                    call_record.status = 'completed'
                    call_record.ended_at = datetime.utcnow()
                    db.commit()
                    logger.info(f"Database updated with S3 URLs")
                else:
                    new_call = Call(
                        call_sid=call_sid,
                        company_id=call_metadata.get('company_id'),
                        from_number=call_metadata.get('from_number'),
                        to_number=call_metadata.get('to_number'),
                        status='completed',
                        duration=call_duration,
                        transcription=s3_urls.get('transcript_url'),
                        created_at=call_metadata['start_time'],
                        ended_at=datetime.utcnow()
                    )
                    db.add(new_call)
                    db.commit()
                    logger.info(f"Call record created")
            
            except Exception as db_error:
                logger.error(f"Database update error: {db_error}")
                db.rollback()
        
        except Exception as upload_error:
            logger.error(f"S3 upload error: {upload_error}")
            import traceback
            logger.error(traceback.format_exc())
        
        intent_router_service.clear_call(call_sid)
        
        try:
            await deepgram_service.close_session(session_id)
        except:
            pass
        
        call_context.pop(call_sid, None)
        db.close()
        
        logger.info(f"Cleanup complete")


@router.post("/initiate-outbound-call")
async def initiate_outbound_call(request: Request):
    """Initiate an outbound call to a customer"""
    try:
        data = await request.json()
        
        to_number = data.get("to_number")
        customer_name = data.get("customer_name", "")
        company_id = data.get("company_id")
        agent_id = data.get("agent_id")
        campaign_id = data.get("campaign_id", "")
        from_number = data.get("from_number", settings.twilio_phone_number)
        
        if not to_number or not company_id or not agent_id:
            return {
                "success": False,
                "error": "Missing required fields: to_number, company_id, agent_id"
            }
        
        logger.info(f"Initiating outbound call to {to_number}")
        logger.info(f"Customer: {customer_name}, Company: {company_id}, Agent: {agent_id}")
        logger.info(f"Campaign: {campaign_id}")
        
        # Build the callback URL with query parameters
        ws_domain = settings.base_url
        callback_url = (
            f"{ws_domain}/api/v1/twilio-elevenlabs/outbound-connect"
            f"?company_id={company_id}"
            f"&agent_id={agent_id}"
            f"&customer_name={quote(customer_name)}"
            f"&campaign_id={campaign_id}"
        )
        
        logger.info(f"Callback URL: {callback_url}")
        
        # Initiate the call using Twilio
        call = twilio_client.calls.create(
            to=to_number,
            from_=from_number,
            url=callback_url,
            method='POST',
            status_callback=f"{ws_domain}/api/v1/twilio/call-status",
            status_callback_event=['initiated', 'ringing', 'answered', 'completed'],
            status_callback_method='POST',
            record=False,  # We handle recording via stream
            timeout=30,  # Ring timeout in seconds
            machine_detection='DetectMessageEnd',  # Detect answering machines
            machine_detection_timeout=5,
            machine_detection_speech_threshold=2000,
            machine_detection_speech_end_threshold=1200,
            machine_detection_silence_timeout=5000
        )
        
        logger.info(f"Call initiated successfully: {call.sid}")
        
        # Store initial call record in database
        db = SessionLocal()
        try:
            new_call = Call(
                call_sid=call.sid,
                company_id=company_id,
                from_number=from_number,
                to_number=to_number,
                call_type=CallType.outgoing,
                status='initiated',
                created_at=datetime.utcnow()
            )
            db.add(new_call)
            db.commit()
            logger.info(f"Call record created in database: {call.sid}")
        except Exception as db_error:
            logger.error(f"Failed to create call record: {db_error}")
            db.rollback()
        finally:
            db.close()
        
        return {
            "success": True,
            "call_sid": call.sid,
            "to_number": to_number,
            "from_number": from_number,
            "customer_name": customer_name,
            "campaign_id": campaign_id,
            "status": call.status
        }
        
    except Exception as e:
        logger.error(f"Error initiating outbound call: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        return {
            "success": False,
            "error": str(e)
        }

