from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database.config import SessionLocal
from database.models import ConversationTurn
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

    call_metadata = {
        'start_time': datetime.utcnow(),
        'from_number': None,
        'to_number': None
    }

    # Get context from stored data
    context = call_context.get(call_sid, {})
    company_id = context.get("company_id")
    master_agent_id = context.get("agent_id")
    
    logger.info(f"Company ID: {company_id}, Master Agent: {master_agent_id}")
    master_agent = await agent_config_service.get_master_agent(company_id, master_agent_id)

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
            if is_agent_speaking or not transcript.strip():
                return
            
            logger.info(f"USER SAID: '{transcript}'")

            conversation_transcript.append({
                'role': 'user',
                'content': transcript,
                'timestamp': datetime.utcnow().isoformat()
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
                response_chunks = []
                async for chunk in rag.get_answer(
                    company_id=company_id,
                    question=transcript,
                    agent_id=current_agent_id,
                    call_sid=call_sid
                ):
                    response_chunks.append(chunk)
                
                llm_response = "".join(response_chunks)
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
                    content=llm_response,
                    created_at=datetime.utcnow()
                ))
                db.commit()
                
                # Stream audio
                await stream_elevenlabs_audio(websocket, stream_sid, llm_response)
                
            except Exception as e:
                logger.error(f"RAG error: {e}")
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
            await asyncio.sleep(0.8)
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
                        
                        await asyncio.sleep(0.8)
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
        try:
            call_duration = 0
            if call_metadata.get('start_time'):
                call_duration = int((datetime.utcnow() - call_metadata['start_time']).total_seconds())
            
            logger.info(f"Uploading call data to S3...")
            logger.info(f"Duration: {call_duration}s")
            logger.info(f"Transcript turns: {len(conversation_transcript)}")
            
            recording_url = None
            if call_duration > 5:
                logger.info(f"Waiting for Twilio recording to process...")
                
                max_retries = 3
                retry_delay = 3
                
                for attempt in range(max_retries):
                    await asyncio.sleep(retry_delay)
                    
                    recording_url = await call_recording_service.get_recording_url_from_twilio(call_sid)
                    
                    if recording_url:
                        logger.info(f"Recording found on attempt {attempt + 1}")
                        break
                    else:
                        logger.info(f"Recording not ready yet (attempt {attempt + 1}/{max_retries})")
                
                if not recording_url:
                    logger.warning(f"Recording not available after {max_retries} attempts")
            else:
                logger.info(f"Call too short ({call_duration}s), skipping recording")

            s3_urls = await call_recording_service.save_call_data(
                call_sid=call_sid,
                company_id=company_id,
                agent_id=master_agent_id,
                transcript=conversation_transcript,
                recording_url=recording_url,
                duration=call_duration,
                from_number=call_metadata.get('from_number'),
                to_number=call_metadata.get('to_number')
            )
            
            logger.info(f"S3 Upload Complete:")
            logger.info(f"Transcript: {s3_urls.get('transcript_url')}")
            logger.info(f"Recording: {s3_urls.get('recording_url')}")

            try:
                call_record = db.query(Call).filter_by(call_sid=call_sid).first()
                if call_record:
                    call_record.transcription = s3_urls.get('transcript_url')
                    call_record.recording_url = s3_urls.get('recording_url')
                    call_record.duration = call_duration
                    call_record.status = 'completed'
                    call_record.ended_at = datetime.utcnow()
                    db.commit()
                    logger.info(f"Database updated with S3 URLs")
                else:
                    new_call = Call(
                        call_sid=call_sid,
                        company_id=company_id,
                        from_number=call_metadata.get('from_number'),
                        to_number=call_metadata.get('to_number'),
                        status='completed',
                        duration=call_duration,
                        recording_url=s3_urls.get('recording_url'),
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
async def handle_outbound_connect(
    request: Request,
    db: Session = Depends(get_db)
):
    """Outbound call with ElevenLabs"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        
        campaign_id = request.query_params.get("campaign_id")
        agent_id = request.query_params.get("agent_id")
        company_id = request.query_params.get("company_id")
        customer_name = request.query_params.get("customer_name", "")
        
        logger.info(f"📞 Outbound ElevenLabs call: {call_sid}")
        
        response = VoiceResponse()
        
        # Connect to Media Stream
        connect = Connect()
        ws_domain = settings.base_url.replace('https://', '').replace('http://', '')
        stream_url = f"wss://{ws_domain}/api/v1/twilio-elevenlabs/outbound-stream?call_sid={call_sid}&campaign_id={campaign_id}&company_id={company_id}&customer_name={customer_name}"
        
        connect.stream(url=stream_url)
        response.append(connect)
        
        return Response(content=str(response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        response = VoiceResponse()
        response.say("An error occurred.", voice="Polly.Joanna")
        return Response(content=str(response), media_type="application/xml")

@router.websocket("/outbound-stream")
async def handle_outbound_stream(
    websocket: WebSocket,
    call_sid: str,
    campaign_id: str,
    company_id: str,
    customer_name: str
):
    """Handle outbound call streaming"""
    await websocket.accept()
    logger.info(f"🔌 Outbound WebSocket: {call_sid}")
    
    deepgram_service = DeepgramWebSocketService()
    stream_sid = None
    is_agent_speaking = False
    greeting_sent = False
    
    try:
        async def on_transcript(session_id: str, transcript: str):
            nonlocal is_agent_speaking
            
            if is_agent_speaking or not transcript.strip():
                return
            
            logger.info(f"📝 Customer: '{transcript}'")
            
            # Handle booking responses here
            # (You can add booking logic similar to outbound_routes.py)
            
        session_id = f"outbound_{call_sid}"
        await deepgram_service.initialize_session(session_id, on_transcript)
        
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                data = json.loads(message)
                event = data.get("event")
                
                if event == "start":
                    stream_sid = data.get("streamSid")
                    
                    if not greeting_sent:
                        await asyncio.sleep(0.8)
                        greeting = f"Hello {customer_name}! This is Sarah from Callsure. I'm reaching out to schedule a consultation call. Do you have 15 minutes available this week?"
                        await stream_elevenlabs_audio(websocket, stream_sid, greeting)
                        greeting_sent = True
                    
                elif event == "media":
                    if not is_agent_speaking:
                        payload = data.get("media", {}).get("payload")
                        if payload:
                            audio = await deepgram_service.convert_twilio_audio(payload, session_id)
                            await deepgram_service.process_audio_chunk(session_id, audio)
                        
                elif event == "stop":
                    break
                    
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
                
    except Exception as e:
        logger.error(f"Outbound stream error: {str(e)}")
    finally:
        await deepgram_service.close_session(session_id)
