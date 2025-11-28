from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database.config import SessionLocal
from database.models import CallType, ConversationTurn
from services.speech.deepgram_ws_service import DeepgramWebSocketService
from services.voice.elevenlabs_service import elevenlabs_service
from services.rag.rag_service import get_rag_service
from twilio.twiml.voice_response import VoiceResponse, Connect
from datetime import datetime
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

    try:
        logger.info("WebSocket connection attempt...")
        await websocket.accept()
        logger.info("WebSocket ACCEPTED")
        
    except Exception as e:
        logger.error(f"Failed to accept WebSocket: {str(e)}")
        return
    
    conversation_transcript = []
    call_sid = websocket.query_params.get("call_sid")
    logger.info(f"Query params: {dict(websocket.query_params)}")
    
    first_message_data = None
    
    if not call_sid:
        logger.info("No call_sid in query, waiting for Twilio 'start' event...")
        
        try:
            # Wait for Twilio to send the 'start' event
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

    logger.info(f"Master: {master_agent['name']}")
    logger.info(f"Specialized agents: {[a['name'] for a in specialized_agents]}")

    # Initialize services
    db = SessionLocal()
    deepgram_service = DeepgramWebSocketService()
    rag = get_rag_service()
    
    stream_sid = None
    is_agent_speaking = False
    greeting_sent = False
    call_state = {
        "first_interaction": True,
        "interaction_count": 0
    }
    
    try:
        async def on_deepgram_transcript(session_id: str, transcript: str):
            nonlocal conversation_transcript
            nonlocal is_agent_speaking
            nonlocal current_agent_context

            if is_agent_speaking or not transcript.strip():
                return
            
            logger.info(f"USER SAID: '{transcript}'")

            conversation_transcript.append({
                'role': 'user',
                'content': transcript,
                'timestamp': datetime.utcnow().isoformat()
            })

            sentiment_analysis = prompt_template_service.detect_sentiment_and_urgency(
                transcript,
                current_agent_context
            )

            logger.info(f"Sentiment: {sentiment_analysis['sentiment']}, Urgency: {sentiment_analysis['urgency']}")

            conversation_transcript.append({
                'role': 'user',
                'content': transcript,
                'timestamp': datetime.utcnow().isoformat(),
                'sentiment': sentiment_analysis['sentiment'],
                'urgency': sentiment_analysis['urgency']
            })

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

            urgent_acknowledgment = None
            if sentiment_analysis['urgency'] == 'high' and sentiment_analysis['suggested_action']:
                is_agent_speaking = True
                urgent_acknowledgement = sentiment_analysis['suggested_action']
                
                logger.info(f"URGENT RESPONSE: '{urgent_response}'")
                
                await stream_elevenlabs_audio(websocket, stream_sid, urgent_response)
                is_agent_speaking = False

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
                            await stream_elevenlabs_audio(websocket, stream_sid, routing_message)
                            await asyncio.sleep(0.5)
                            is_agent_speaking = False
                        else:
                            logger.info(f"ROUTED to {agent_info['name']}")
                else:
                    logger.info(f"Staying with MASTER")
            
            call_state["interaction_count"] += 1
            
            is_agent_speaking = True
            
            try:
                await asyncio.sleep(1)
                if urgent_acknowledgment:
                    acknowledgment = "Let me check what we can do to help you with this immediately."
                else:
                    acknowledgment = prompt_template_service.generate_rag_acknowledgment(
                        transcript,
                        current_agent_context
                    )
                
                logger.info(f"RAG Acknowledgment: '{acknowledgment}'")

                await stream_elevenlabs_audio(websocket, stream_sid, acknowledgment)

                await asyncio.sleep(0.3)

                conversation_messages = []
                for msg in conversation_transcript[-10:]:
                    if msg['role'] in ['user', 'assistant']:
                        conversation_messages.append({
                            'role': msg['role'],
                            'content': msg['content']
                        })

                if sentiment_analysis['urgency'] == 'high':
                    conversation_messages.insert(0, {
                        'role': 'system',
                        'content': f"""[URGENT REQUEST DETECTED]
        Keywords: {', '.join(sentiment_analysis['urgency_keywords'])}
        Priority: HIGH
        Instructions: 
        1. First, try to find a solution in the available documentation
        2. If documentation has a solution, provide it immediately with clear steps
        3. If NO solution exists in documentation, automatically create a support ticket
        4. Inform customer about the ticket and escalation
        5. Maintain empathy and urgency throughout"""
                    })

                response_chunks = []
                async for chunk in rag.get_answer(
                    company_id=company_id,
                    question=transcript,
                    agent_id=current_agent_id,
                    call_sid=call_sid,
                    conversation_context=conversation_messages
                ):
                    response_chunks.append(chunk)
                
                llm_response = "".join(response_chunks)
                if sentiment_analysis['urgency'] == 'high' and not rag_found_solution:
                    logger.warning(f"No RAG solution for urgent request - creating ticket")
                    
                    ticket_result = await execute_function(
                        function_name="create_ticket",
                        arguments={
                            "title": f"Urgent: {transcript[:50]}",
                            "description": f"High urgency request: {transcript}\n\nKeywords: {', '.join(sentiment_analysis['urgency_keywords'])}",
                            "customer_phone": call_metadata.get('from_number'),
                            "priority": "high"
                        },
                        company_id=company_id,
                        call_sid=call_sid
                    )
                    
                    llm_response = f"{llm_response} {ticket_result}"

                full_response = f"{urgent_acknowledgment} {acknowledgment} {llm_response}" if urgent_acknowledgment else f"{acknowledgment} {llm_response}"
                conversation_transcript.append({
                    'role': 'assistant',
                    'content': llm_response,
                    'timestamp': datetime.utcnow().isoformat()
                })
                logger.info(f"AGENT ({current_agent_id}): '{llm_response[:100]}...'")
                
                # Save to DB
                db.add(ConversationTurn(
                    call_sid=call_sid,
                    role="assistant",
                    content=full_response,
                    created_at=datetime.utcnow()
                ))
                db.commit()
                
                # Stream audio
                await stream_elevenlabs_audio(websocket, stream_sid, llm_response)
                
            except Exception as e:
                logger.error(f"RAG error: {e}")
                if sentiment_analysis['urgency'] == 'high':
                    logger.error(f"RAG failed for urgent request - creating ticket as fallback")
                    
                    try:                      
                        ticket_result = await execute_function(
                            function_name="create_ticket",
                            arguments={
                                "title": f"Urgent: {transcript[:50]}",
                                "description": f"High urgency request (RAG failed): {transcript}",
                                "customer_phone": call_metadata.get('from_number'),
                                "priority": "critical"
                            },
                            company_id=company_id,
                            call_sid=call_sid
                        )
                        
                        await stream_elevenlabs_audio(websocket, stream_sid, ticket_result)
                    except Exception as ticket_error:
                        logger.error(f"Ticket creation failed: {ticket_error}")
                        await stream_elevenlabs_audio(
                            websocket,
                            stream_sid,
                            "I'm prioritizing your request and connecting you with a supervisor immediately."
                        )
                else:
                    await stream_elevenlabs_audio(
                        websocket,
                        stream_sid,
                        "I'm having trouble. Could you please repeat?"
                    )
            finally:
                is_agent_speaking = False
        
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
        
        # Process buffered start event if we have it
        if first_message_data and first_message_data.get("event") == "start":
            logger.info("Processing buffered start event...")
            stream_sid = first_message_data.get("streamSid")
            logger.info(f"STREAM STARTED: {stream_sid}")
            
            # Send greeting
            greeting = prompt_template_service.generate_greeting(master_agent)
            logger.info(f"Sending greeting: '{greeting}'")
            await stream_elevenlabs_audio(websocket, stream_sid, greeting)
            greeting_sent = True
            logger.info(f"Greeting sent")
        
        # Main message loop
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
                        await stream_elevenlabs_audio(websocket, stream_sid, greeting)
                        greeting_sent = True
                        logger.info(f"Greeting sent")
                    
                elif event == "media":
                    if not is_agent_speaking:
                        payload = data.get("media", {}).get("payload")
                        if payload:
                            audio = await deepgram_service.convert_twilio_audio(payload, session_id)
                            if audio:
                                await deepgram_service.process_audio_chunk(session_id, audio)
                    
                elif event == "mark":
                    mark_name = data.get("mark", {}).get("name")
                    logger.debug(f"Mark: {mark_name}")
                    
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
        # Cleanup
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

            recording_url = None

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

async def stream_elevenlabs_audio(websocket: WebSocket, stream_sid: str, text: str):
    """Stream ElevenLabs audio to Twilio"""
    if not stream_sid:
        logger.error("No stream_sid")
        return
    
    try:
        logger.info(f"Generating audio: '{text[:50]}...'")
        
        chunk_count = 0
        async for audio_chunk in elevenlabs_service.generate(text):
            if audio_chunk and stream_sid:
                message = {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": audio_chunk}
                }
                await websocket.send_json(message)
                chunk_count += 1
        
        logger.info(f"Sent {chunk_count} audio chunks")
        
    except Exception as e:
        logger.error(f"Error streaming audio: {str(e)}")

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
    """Outbound call streaming - mirrors media-stream with full agent routing"""
    
    try:
        logger.info("WebSocket connection attempt...")
        await websocket.accept()
        logger.info("WebSocket ACCEPTED")
    except Exception as e:
        logger.error(f"Failed to accept WebSocket: {str(e)}")
        return
    
    conversation_transcript = []
    call_sid = websocket.query_params.get("call_sid")
    logger.info(f"Query params: {dict(websocket.query_params)}")
    
    first_message_data = None
    
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
    is_agent_speaking = False
    greeting_sent = False

    call_state = {
        "first_interaction": True,
        "interaction_count": 0
    }
    
    try:
        async def on_deepgram_transcript(session_id: str, transcript: str):
            nonlocal conversation_transcript
            nonlocal is_agent_speaking
            nonlocal current_agent_context
            
            if is_agent_speaking or not transcript.strip():
                return
            
            logger.info(f"CUSTOMER SAID: '{transcript}'")

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
            
            if call_type == "outgoing" and should_book and buying_readiness >= 80:
                logger.info(f"AUTO-BOOKING TRIGGERED (AI Confidence: {buying_readiness}%)")
                
                is_agent_speaking = True

                # Hardcoded for testing, change it
                from datetime import timedelta
                tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
                default_time = "10:00 AM"
                
                try:
                    booking_result = await execute_function(
                        function_name="create_booking",
                        arguments={
                            "customer_name": customer_name,
                            "customer_phone": call_metadata.get('to_number'),
                            "preferred_date": tomorrow,
                            "preferred_time": default_time,
                            "customer_email": f"{customer_name.replace(' ', '').lower()}@temp.com",
                            "notes": f"Auto-booked from outbound campaign {campaign_id}"
                        },
                        company_id=company_id,
                        call_sid=call_sid,
                        campaign_id=campaign_id
                    )
                    
                    logger.info(f"AUTO-BOOKING RESULT: {booking_result}")

                    conversation_transcript.append({
                        'role': 'assistant',
                        'content': booking_result,
                        'timestamp': datetime.utcnow().isoformat(),
                        'auto_booked': True
                    })

                    db.add(ConversationTurn(
                        call_sid=call_sid,
                        role="assistant",
                        content=booking_result,
                        created_at=datetime.utcnow()
                    ))
                    db.commit()

                    await stream_elevenlabs_audio(websocket, stream_sid, booking_result)
                    
                    is_agent_speaking = False
                    return
                    
                except Exception as booking_error:
                    logger.error(f"Auto-booking failed: {booking_error}")

            elif call_type == "outgoing" and intent_type == 'rejection' and not should_persuade:
                logger.info(f"HARD REJECTION DETECTED - Final attempt to re-engage")
                
                is_agent_speaking = True
                
                # One last persuasion attempt
                farewell_persuasion = (
                    f"I completely understand, {customer_name}. "
                    f"Just so you know, we're offering a special promotion this week. "
                    f"Would you like to hear about it quickly before I let you go?"
                )
                
                conversation_transcript.append({
                    'role': 'assistant',
                    'content': farewell_persuasion,
                    'timestamp': datetime.utcnow().isoformat(),
                    'rejection_handled': True
                })
                
                try:
                    db.add(ConversationTurn(
                        call_sid=call_sid,
                        role="assistant",
                        content=farewell_persuasion,
                        created_at=datetime.utcnow()
                    ))
                    db.commit()
                except Exception as e:
                    logger.error(f"DB error: {e}")
                
                await stream_elevenlabs_audio(websocket, stream_sid, farewell_persuasion)
                
                is_agent_speaking = False
                return

            else:
                logger.info(f"PERSUASION MODE (Intent: {intent_type}, Readiness: {buying_readiness}%)")

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
                            await stream_elevenlabs_audio(websocket, stream_sid, routing_message)
                            await asyncio.sleep(0.5)
                            is_agent_speaking = False
                        else:
                            logger.info(f"ROUTED to {agent_info['name']}")
                else:
                    logger.info(f"   Staying with MASTER")
            
            call_state["interaction_count"] += 1
            
            is_agent_speaking = True
            
            try:
                await asyncio.sleep(1)
                objection_type = intent_analysis.get('objection_type', 'none')
                
                if intent_type == 'objection':
                    if objection_type == 'price':
                        acknowledgment = "I understand cost is important. Let me show you the value we offer."
                    elif objection_type == 'time':
                        acknowledgment = "I appreciate you're busy. Let me quickly highlight the key benefits."
                    elif objection_type == 'trust':
                        acknowledgment = "That's a fair concern. Let me share what our customers say."
                    else:
                        acknowledgment = "I hear your concern. Let me address that for you."
                elif intent_type == 'soft_interest':
                    acknowledgment = "Great! Let me share more details that I think you'll find valuable."
                elif intent_type == 'question':
                    acknowledgment = "Excellent question! Let me get you that information."
                else:
                    acknowledgment = prompt_template_service.generate_rag_acknowledgment(
                        transcript,
                        current_agent_context
                    )
                
                logger.info(f"RAG Acknowledgment: '{acknowledgment}'")
                
                await stream_elevenlabs_audio(websocket, stream_sid, acknowledgment)
                await asyncio.sleep(0.3)

                conversation_messages = []
                for msg in conversation_transcript[-10:]:
                    if msg['role'] in ['user', 'assistant']:
                        conversation_messages.append({
                            'role': msg['role'],
                            'content': msg['content']
                        })

                if call_type == "outgoing":
                    persuasion_context = f"""[AI INTENT ANALYSIS]
    Customer Intent: {intent_type}
    Sentiment: {intent_analysis.get('sentiment')}
    Buying Readiness: {buying_readiness}%
    Objection Type: {objection_type}
    Reasoning: {intent_analysis.get('reasoning')}

    [SALES STRATEGY]
    - Intent suggests: {"CLOSE THE SALE" if buying_readiness >= 80 else "OVERCOME OBJECTIONS" if intent_type == 'objection' else "BUILD INTEREST"}
    - Tone: {intent_analysis.get('suggested_response_tone')}
    - Focus on addressing the customer's specific concern
    - {"Be enthusiastic and confirm booking details" if buying_readiness >= 80 else "Be empathetic and build trust" if intent_type == 'objection' else "Be informative and highlight benefits"}
    """
                
                conversation_messages.insert(0, {
                    'role': 'system',
                    'content': persuasion_context
                })
                
     
                if call_type == "outgoing" and campaign_id:
                    conversation_messages.insert(0, {
                        'role': 'system',
                        'content': f"[Call metadata: campaign_id: {campaign_id}, customer: {customer_name}]"
                    })

                response_chunks = []
                async for chunk in rag.get_answer(
                    company_id=company_id,
                    question=transcript,
                    agent_id=current_agent_id,
                    call_sid=call_sid,
                    conversation_context=conversation_messages
                ):
                    response_chunks.append(chunk)
                
                llm_response = "".join(response_chunks)
                full_response = f"{acknowledgment} {llm_response}"
                
                # Save to transcript
                conversation_transcript.append({
                    'role': 'assistant',
                    'content': full_response,
                    'timestamp': datetime.utcnow().isoformat()
                })
                
                logger.info(f"AGENT ({current_agent_id}): '{llm_response[:100]}...'")
                
                # Save to DB
                db.add(ConversationTurn(
                    call_sid=call_sid,
                    role="assistant",
                    content=full_response,
                    created_at=datetime.utcnow()
                ))
                db.commit()
                
                # Stream audio
                await stream_elevenlabs_audio(websocket, stream_sid, llm_response)
                
            except Exception as e:
                logger.error(f"RAG error: {e}")
                import traceback
                logger.error(traceback.format_exc())
                await stream_elevenlabs_audio(
                    websocket,
                    stream_sid,
                    "I'm having trouble. Could you please repeat?"
                )
            finally:
                is_agent_speaking = False
        
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
            
            greeting = prompt_template_service.generate_outbound_sales_greeting(
                agent=master_agent,
                customer_name=customer_name,
                campaign_id=campaign_id
            )
            
            logger.info(f"Sending dynamic greeting: '{greeting}'")
            
            # Save greeting to transcript
            conversation_transcript.append({
                'role': 'assistant',
                'content': greeting,
                'timestamp': datetime.utcnow().isoformat()
            })
            
            await stream_elevenlabs_audio(websocket, stream_sid, greeting)
            greeting_sent = True
            logger.info(f"Greeting sent")
        
        # Main message loop
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

                        greeting = prompt_template_service.generate_outbound_sales_greeting(
                            agent=master_agent,
                            customer_name=customer_name,
                            campaign_id=campaign_id
                        )
                        logger.info(f"Sending greeting: '{greeting}'")
                        
                        conversation_transcript.append({
                            'role': 'assistant',
                            'content': greeting,
                            'timestamp': datetime.utcnow().isoformat()
                        })
                        
                        await stream_elevenlabs_audio(websocket, stream_sid, greeting)
                        greeting_sent = True
                        logger.info(f"Greeting sent")
                    
                elif event == "media":
                    if not is_agent_speaking:
                        payload = data.get("media", {}).get("payload")
                        if payload:
                            audio = await deepgram_service.convert_twilio_audio(payload, session_id)
                            if audio:
                                await deepgram_service.process_audio_chunk(session_id, audio)
                    
                elif event == "mark":
                    mark_name = data.get("mark", {}).get("name")
                    logger.debug(f"Mark: {mark_name}")
                    
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
        # Cleanup
        logger.info(f"Cleaning up {call_sid}")
        logger.info(f"From: {call_metadata.get('from_number')}")
        logger.info(f"To: {call_metadata.get('to_number')}")
        logger.info(f"Company ID: {call_metadata.get('company_id')}")
        logger.info(f"Call Type: {call_metadata.get('call_type')}")
        
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
                    call_record.company_id = company_id
                    call_record.from_number = call_metadata.get('from_number')
                    call_record.to_number = call_metadata.get('to_number')
                    call_record.call_type = CallType.outgoing
                    call_record.duration = call_duration
                    call_record.status = 'completed'
                    call_record.ended_at = datetime.utcnow()
                    db.commit()
                    logger.info(f"Database updated (outgoing call)")
                else:
                    new_call = Call(
                        call_sid=call_sid,
                        company_id=company_id,
                        from_number=call_metadata.get('from_number'),
                        to_number=call_metadata.get('to_number'),
                        call_type=CallType.outgoing,
                        status='completed',
                        duration=call_duration,
                        transcription=s3_urls.get('transcript_url'),
                        created_at=call_metadata['start_time'],
                        ended_at=datetime.utcnow()
                    )
                    db.add(new_call)
                    db.commit()
                    logger.info(f"Outbound call record created")
            except Exception as db_error:
                logger.error(f"Database update error: {db_error}")
                import traceback
                logger.error(traceback.format_exc())
                db.rollback()
                
        except Exception as upload_error:
            logger.error(f"S3 upload error: {upload_error}")
            import traceback
            logger.error(traceback.format_exc())
        
        # Final cleanup
        intent_router_service.clear_call(call_sid)
        
        try:
            await deepgram_service.close_session(session_id)
        except:
            pass
        
        call_context.pop(call_sid, None)
        db.close()
        logger.info(f"Cleanup complete")

class OutboundCallRequest(BaseModel):
    to_number: str = Field(..., description="Phone number with country code")
    company_id: str = Field(..., description="Company UUID")
    agent_id: str = Field(..., description="Agent UUID")
    customer_name: str = Field(default="", description="Customer name for greeting")
    campaign_id: str = Field(default="", description="Campaign ID (optional)")

@router.post("/initiate-outbound-call")
async def initiate_outbound_call(
    request: OutboundCallRequest,
    db: Session = Depends(get_db)
):
    """
    Initiate an outbound call via Twilio
    
    Test with Postman:
    POST https://processor.callsure.ai/api/v1/twilio-elevenlabs/initiate-outbound-call
    
    Body (JSON):
    {
        "to_number": "+919830608824",
        "company_id": "335daf57-3121-43f5-8776-03bf4c70eaae",
        "agent_id": "43fca54b-eee1-4b1d-8e40-58fb5d23ec98",
        "customer_name": "John Doe",
        "campaign_id": "test-campaign-001"
    }
    """
    try:
        logger.info(f"Initiating outbound call to {request.to_number}")
        logger.info(f"Company: {request.company_id}")
        logger.info(f"Agent: {request.agent_id}")
        logger.info(f"Customer: {request.customer_name}")

        from_number = settings.twilio_phone_number

        base_url = settings.base_url
        callback_url = f"{base_url}/api/v1/twilio-elevenlabs/outbound-connect"
        callback_url += f"?company_id={request.company_id}"
        callback_url += f"&agent_id={request.agent_id}"
        callback_url += f"&customer_name={quote(request.customer_name)}"
        if request.campaign_id:
            callback_url += f"&campaign_id={request.campaign_id}"
        
        logger.info(f"Callback URL: {callback_url}")

        call = twilio_client.calls.create(
            to=request.to_number,
            from_=from_number,
            url=callback_url,
            method='POST',
            status_callback=f"{base_url}/api/v1/twilio/call-status",
            status_callback_event=['initiated', 'ringing', 'answered', 'completed'],
            status_callback_method='POST',
            record=True,
            timeout=30
        )
        
        logger.info(f"Call initiated successfully")
        logger.info(f"Call SID: {call.sid}")
        logger.info(f"Status: {call.status}")
        
        try:
            call_record = Call(
                call_sid=call.sid,
                company_id=request.company_id,
                from_number=from_number,
                to_number=request.to_number,
                call_type=CallType.outgoing,
                status='initiated',
                created_at=datetime.utcnow()
            )
            db.add(call_record)
            db.commit()
            logger.info(f"Call record created in database")
        except Exception as db_error:
            logger.error(f"Database error: {db_error}")
            db.rollback()
        
        return {
            "success": True,
            "message": "Outbound call initiated successfully",
            "data": {
                "call_sid": call.sid,
                "to_number": request.to_number,
                "from_number": from_number,
                "status": call.status,
                "customer_name": request.customer_name,
                "company_id": request.company_id,
                "agent_id": request.agent_id
            }
        }
        
    except Exception as e:
        logger.error(f"Error initiating outbound call: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        return {
            "success": False,
            "message": f"Failed to initiate call: {str(e)}",
            "data": None
        }