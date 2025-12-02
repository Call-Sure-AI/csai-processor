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
from services.company_service import company_service
from twilio.twiml.voice_response import VoiceResponse, Connect
from datetime import datetime, timedelta
from config.settings import settings
from database.config import get_db
import logging
import json
import asyncio
import time
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
from services.slot_manager_service import SlotManagerService

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


@router.post("/call-status")
async def handle_call_status(request: Request):
    """Handle Twilio call status callbacks"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        call_status = form_data.get("CallStatus")
        
        logger.info(f"📞 Call status update: {call_sid} -> {call_status}")
        
        # Update database
        db = SessionLocal()
        try:
            call_record = db.query(Call).filter_by(call_sid=call_sid).first()
            if call_record:
                call_record.status = call_status
                if call_status == 'completed':
                    call_record.ended_at = datetime.utcnow()
                db.commit()
                logger.info(f"✅ Updated call {call_sid} status to {call_status}")
        except Exception as e:
            logger.error(f"DB error: {e}")
            db.rollback()
        finally:
            db.close()
        
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error handling call status: {e}")
        return {"status": "error", "message": str(e)}


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
            # ✅ CHECK STOP FLAG BEFORE EVERY CHUNK
            if stop_flag_ref.get('stop', False):
                logger.warning(f"🛑 STOP FLAG DETECTED - Halting audio stream at chunk {chunk_count}")
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
        check_interval = 0.05  # Check every 50ms for faster response
        
        while elapsed < playback_duration:
            if stop_flag_ref.get('stop', False):
                logger.warning(f"🛑 STOP FLAG DETECTED during playback at {elapsed:.1f}s")
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
        logger.warning(f"🛑 Audio+playback cancelled at chunk {chunk_count}")
        try:
            clear_message = {"event": "clear", "streamSid": stream_sid}
            await websocket.send_json(clear_message)
            await asyncio.sleep(0.5)
        except:
            pass
        raise
    except Exception as e:
        logger.error(f"Error streaming audio: {e}")


async def stream_elevenlabs_audio_with_playback_and_timing(
    websocket: WebSocket, 
    stream_sid: str, 
    text: str, 
    stop_flag_ref: dict,
    audio_timing_ref: dict
):
    """Stream audio with ACCURATE timing tracking based on actual chunks"""
    if not stream_sid:
        logger.error("No stream_sid")
        return
    
    try:
        logger.info(f"🔊 Generating audio: '{text[:50]}...'")
        chunk_count = 0
        
        # Stream audio chunks
        async for audio_chunk in elevenlabs_service.generate(text):
            if stop_flag_ref.get('stop', False):
                logger.warning(f"🛑 STOP FLAG DETECTED - Halting audio stream at chunk {chunk_count}")
                try:
                    clear_message = {"event": "clear", "streamSid": stream_sid}
                    await websocket.send_json(clear_message)
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
        
        # ✅ NOW calculate ACCURATE playback duration based on actual chunks
        playback_duration = (chunk_count * 0.02) + 0.5
        audio_timing_ref['expected_end'] = time.time() + playback_duration
        
        logger.info(f"⏳ Simulating playback: {playback_duration:.1f}s (expected end: {audio_timing_ref['expected_end']:.2f})")
        
        # Wait for playback while checking for interruption
        elapsed = 0
        check_interval = 0.05
        
        while elapsed < playback_duration:
            if stop_flag_ref.get('stop', False):
                logger.warning(f"🛑 STOP FLAG DETECTED during playback at {elapsed:.1f}s")
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
        logger.warning(f"🛑 Audio+playback cancelled at chunk {chunk_count}")
        try:
            clear_message = {"event": "clear", "streamSid": stream_sid}
            await websocket.send_json(clear_message)
            await asyncio.sleep(0.5)
        except:
            pass
        raise
    except Exception as e:
        logger.error(f"Error streaming audio: {e}")


@router.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Incoming call handler with proper echo prevention and interruption handling"""
    try:
        logger.info("WebSocket connection attempt...")
        await websocket.accept()
        logger.info("✅ WebSocket ACCEPTED")
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
    agent_name = master_agent["name"]
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
        # ✅ INSTANT INTERRUPTION CALLBACK
        async def on_interim_transcript(session_id: str, transcript: str, confidence: float):
            """🚨 INSTANT interruption detection - fires on interim transcripts (<200ms)"""
            nonlocal is_agent_speaking
            nonlocal stop_audio_flag
            nonlocal current_audio_task
            nonlocal current_processing_task
            
            # Only check if agent is speaking
            if not is_agent_speaking:
                return
            
            # Check word count
            word_count = len(transcript.split())
            if word_count >= 2:
                logger.warning(f"🚨 INTERRUPTION DETECTED: '{transcript}' (conf: {confidence:.2f})")
                
                # Set stop flag IMMEDIATELY
                stop_audio_flag['stop'] = True
                logger.info(f"✅ Stop flag set to TRUE")
                
                # Cancel audio playback
                if current_audio_task and not current_audio_task.done():
                    logger.info("⚡ Cancelling audio task...")
                    current_audio_task.cancel()
                    try:
                        await current_audio_task
                    except asyncio.CancelledError:
                        logger.info("✅ Audio task cancelled")
                
                # Cancel processing
                if current_processing_task and not current_processing_task.done():
                    logger.info("⚡ Cancelling processing task...")
                    current_processing_task.cancel()
                    try:
                        await current_processing_task
                    except asyncio.CancelledError:
                        logger.info("✅ Processing task cancelled")
                
                # Mark agent as not speaking
                is_agent_speaking = False
                
                # Send clear command to Twilio
                try:
                    clear_message = {"event": "clear", "streamSid": stream_sid}
                    await websocket.send_json(clear_message)
                    logger.info("✅ Sent clear command to Twilio")
                except Exception as e:
                    logger.error(f"Error sending clear: {e}")
                
                # Wait for buffer to clear
                await asyncio.sleep(0.5)
                
                # Reset flag
                stop_audio_flag['stop'] = False
                logger.info("✅ Ready for customer input")
        
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
            
            logger.info(f"👤 CUSTOMER SAID: '{transcript}'")
            
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
            
            # Agent routing
            current_agent_id = master_agent_id
            
            if specialized_agents:
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
                            logger.info(f"🔀 RE-ROUTING to {agent_info['name']}")
                            
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
                    urgent_acknowledgment=None,
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
        
        # Initialize Deepgram with BOTH callbacks
        session_id = f"deepgram_{call_sid}"
        logger.info(f"🎙️ Initializing Deepgram...")
        
        deepgram_connected = await deepgram_service.initialize_session(
            session_id=session_id,
            callback=on_deepgram_transcript,
            interruption_callback=on_interim_transcript  # ✅ CRITICAL
        )
        
        if not deepgram_connected:
            logger.error(f"Deepgram connection failed")
            await websocket.close()
            return
        
        logger.info(f"✅ Deepgram connected")
        
        if first_message_data and first_message_data.get("event") == "start":
            stream_sid = first_message_data.get("streamSid")
            logger.info(f"✅ STREAM STARTED: {stream_sid}")
            
            greeting = prompt_template_service.generate_greeting(master_agent, company_id, agent_name)
            logger.info(f"💬 Sending greeting: '{greeting[:50]}...'")
            
            is_agent_speaking = True
            stop_audio_flag['stop'] = False
            greeting_start_time = datetime.utcnow()
            
            current_audio_task = asyncio.create_task(
                stream_elevenlabs_audio_with_playback(websocket, stream_sid, greeting, stop_audio_flag)
            )
            
            try:
                await current_audio_task
                logger.info("✅ Greeting completed - ready for customer")
            except asyncio.CancelledError:
                logger.info("Greeting cancelled")
            finally:
                is_agent_speaking = False
                current_audio_task = None
            
            greeting_sent = True
            await asyncio.sleep(0.5)
        
        logger.info("📡 Entering message loop...")
        
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
                        logger.info(f"✅ STREAM STARTED: {stream_sid}")
                        
                        greeting = prompt_template_service.generate_greeting(master_agent, company_id, agent_name)
                        
                        is_agent_speaking = True
                        stop_audio_flag['stop'] = False
                        greeting_start_time = datetime.utcnow()
                        
                        current_audio_task = asyncio.create_task(
                            stream_elevenlabs_audio_with_playback(websocket, stream_sid, greeting, stop_audio_flag)
                        )
                        
                        try:
                            await current_audio_task
                        except asyncio.CancelledError:
                            pass
                        finally:
                            is_agent_speaking = False
                            current_audio_task = None
                        
                        greeting_sent = True
                        await asyncio.sleep(0.5)
                
                elif event == "media":
                    # ✅ ALWAYS process audio for real-time interruption
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
        logger.info(f"🧹 Cleaning up {call_sid}")
        
        try:
            call_duration = 0
            if call_metadata.get('start_time'):
                call_duration = int((datetime.utcnow() - call_metadata['start_time']).total_seconds())
            
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
            except Exception as db_error:
                logger.error(f"Database update error: {db_error}")
                db.rollback()
        
        except Exception as upload_error:
            logger.error(f"S3 upload error: {upload_error}")
        
        intent_router_service.clear_call(call_sid)
        
        try:
            await deepgram_service.close_session(session_id)
        except:
            pass
        
        call_context.pop(call_sid, None)
        db.close()


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
        
        # Build conversation context
        conversation_messages = []
        for msg in conversation_transcript[-10:]:
            if msg['role'] in ['user', 'assistant']:
                conversation_messages.append({
                    'role': msg['role'],
                    'content': msg['content']
                })
        
        # Strategy 1: Direct canned response
        if response_strategy == 'direct_canned':
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
        
        # Strategy 2: Use conversation context only
        elif response_strategy == 'conversation_context':
            support_context = f"""[INCOMING SUPPORT CALL - ACTIVE CONVERSATION]
Customer's Sentiment: {sentiment_analysis.get('sentiment', 'neutral')}
Urgency: {sentiment_analysis.get('urgency', 'normal')}
Customer's Current Message: "{transcript}"

You are continuing an active conversation. Use the conversation history to respond naturally.

**GUIDELINES:**
- Reference what was already discussed
- Keep responses natural and conversational (2-4 sentences typical)
- Use create_ticket function if they report an issue
- Be helpful and empathetic"""
            
            conversation_messages.insert(0, {
                'role': 'system',
                'content': support_context
            })
            
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
                    user_timezone=call_metadata.get('user_timezone', 'UTC'),
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
            support_context = f"""[INCOMING SUPPORT CALL]
Customer's Sentiment: {sentiment_analysis.get('sentiment', 'neutral')}
Urgency: {sentiment_analysis.get('urgency', 'normal')}
Customer's Current Message: "{transcript}"

You are on a LIVE SUPPORT PHONE CALL. Use the provided documentation to give accurate answers.

**RESPONSE GUIDELINES:**
- If customer asks for details, provide them completely
- Match the customer's urgency and tone
- Use create_ticket function for issues requiring follow-up
- Be solution-focused and clear"""
            
            conversation_messages.insert(0, {
                'role': 'system',
                'content': support_context
            })
            
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
            
            conversation_transcript.append({
                'role': 'assistant',
                'content': llm_response,
                'timestamp': datetime.utcnow().isoformat(),
                'strategy': 'document_retrieval',
                'documents_retrieved': True
            })
        
        else:
            llm_response = "I'm here to help. Could you tell me more about what you need?"
            
            conversation_transcript.append({
                'role': 'assistant',
                'content': llm_response,
                'timestamp': datetime.utcnow().isoformat(),
                'strategy': 'fallback',
                'documents_retrieved': False
            })
        
        logger.info(f"🤖 AGENT: '{llm_response[:100]}...'")
        
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
    call_type: str,
    audio_timing_ref: dict,
    current_audio_task_ref: dict
):
    """
    Outbound call processing with SLOT VALIDATION + OPTIMIZED LATENCY.
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
        
        # Initialize booking
        if should_start_booking and not booking_session:
            booking_session = booking_orchestrator.initialize_booking(
                call_sid=call_sid,
                customer_name=customer_name,
                customer_phone=customer_phone,
                campaign_id=campaign_id
            )
            is_booking_mode = True
            booking_orchestrator.transition_state(call_sid, BookingState.COLLECTING_DATE, "Customer interested")
            logger.info(f"🎫 Booking started for {customer_name}")
        
        is_sales_call = is_booking_mode or buying_readiness >= 50
        
        now = datetime.now()
        
        # ✅ PARALLEL API CALLS FOR SPEED
        async def fetch_datetime_info():
            return await datetime_parser_service.parse_user_datetime(
                user_input=transcript,
                user_timezone=call_metadata.get('user_timezone', 'UTC'),
                business_hours={'start': '09:00', 'end': '18:00'}
            )
        
        async def fetch_slots():
            if not (is_booking_mode and campaign_id):
                return ""
            try:
                slot_manager = SlotManagerService()
                slots_data = await slot_manager.get_available_slots(
                    campaign_id=campaign_id,
                    start_date=now,
                    end_date=now + timedelta(days=7),
                    count=5
                )
                return slot_manager.format_slots_for_prompt(slots_data)
            except Exception as e:
                logger.error(f"Slot fetch error: {e}")
                tomorrow = now + timedelta(days=1)
                return f"Suggest: tomorrow ({tomorrow.strftime('%A, %B %d')}) at 10 AM, 2 PM, or 4 PM"
        
        # Run in parallel
        datetime_info, available_slots_text = await asyncio.gather(
            fetch_datetime_info(),
            fetch_slots(),
            return_exceptions=True
        )
        
        # Handle exceptions
        if isinstance(datetime_info, Exception):
            logger.error(f"Datetime parsing failed: {datetime_info}")
            datetime_info = {}
        if isinstance(available_slots_text, Exception):
            logger.error(f"Slot fetching failed: {available_slots_text}")
            available_slots_text = ""
        
        # Update booking session if datetime found
        if datetime_info.get('parsed_successfully') and booking_session:
            logger.info(f"✓ AI extracted: {datetime_info.get('user_friendly')}")
            
            if datetime_info.get('date'):
                booking_orchestrator.update_session_data(call_sid, 'date', datetime_info['date'])
            if datetime_info.get('time'):
                booking_orchestrator.update_session_data(call_sid, 'time', datetime_info['time'])
            if datetime_info.get('datetime_iso'):
                booking_orchestrator.update_session_data(call_sid, 'datetime_iso', datetime_info['datetime_iso'])
        
        # Extract email if present
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        import re
        email_match = re.search(email_pattern, transcript)
        if email_match and booking_session:
            email = email_match.group(0)
            booking_orchestrator.update_session_data(call_sid, 'email', email)
            logger.info(f"📧 Extracted email: {email}")
        
        # Build conversation context
        conversation_messages = []
        for msg in conversation_transcript[-6:]:
            if msg['role'] in ['user', 'assistant']:
                conversation_messages.append({
                    'role': msg['role'],
                    'content': msg['content']
                })
        
        # Get context
        company_name = company_service.get_company_name_by_id(company_id)
        
        # ✅ IMPROVED SALES PROMPT - Allows answering product questions
        if is_sales_call:
            next_action = booking_orchestrator.get_next_action(call_sid) if booking_session else {'prompt_hint': 'Ask for date/time'}
            collected = booking_session['collected_data'] if booking_session else {}
            customer = booking_session['customer_info'] if booking_session else {}
            
            has_date = collected.get('date') is not None
            has_time = collected.get('time') is not None
            has_email = customer.get('email') is not None
            
            prompt = f"""Sales call for {company_name} with {customer_name}.

**Booking Progress:**
- Name: ✅ {customer_name}
- Phone: ✅ {customer_phone}
- Date: {'✅ ' + collected.get('date') if has_date else '❌ NEED'}
- Time: {'✅ ' + collected.get('time') if has_time else '❌ NEED'}
- Email: {'✅ ' + customer.get('email') if has_email else '❌ NEED'}

**Today:** {now.strftime('%A, %B %d, %Y')}

**Available Time Slots:**
{available_slots_text if available_slots_text else "Suggest: tomorrow 10 AM, 2 PM, or 4 PM"}

**Next step:** {next_action['prompt_hint']}

**CRITICAL RULES:**
1. We already have name and phone - NEVER ask for them
2. Primary goal: Collect date, time, and email to complete booking
3. Flow: date → time → verify slot → email → confirm booking
4. **PRODUCT QUESTIONS**: If customer asks about the product/features/benefits:
   - Give a brief answer (1-2 sentences max)
   - Then immediately guide back to booking: "Would you like to see this in action? Let's schedule that demo."
5. Keep ALL responses to 1-2 sentences maximum
6. NO support tickets in sales mode

**Examples:**
Customer: "What does your product do?"
You: "We help businesses automate their sales calls with AI. Want to see how it works for your team? What time works best?"

Customer: "How much does it cost?"
You: "Pricing varies by plan - I can show you options that fit your needs in the demo. When would you like to schedule it?"

Customer: "tomorrow at 2pm"
You: Use check_slot_availability function to verify

**After verified slot + email collected:**
Use verify_customer_email function, then create_booking

Be conversational but focused on booking."""

        else:
            # Non-sales mode
            prompt = f"""Sales call for {company_name}.

Customer: {customer_name}
Interest Level: {buying_readiness}%

**Approach based on interest:**
- 70%+: "Would you like to book a demo?"
- 40-70%: Share 1 key benefit + ask booking interest
- <40%: Ask 1 qualifying question

**Rules:**
- Max 1-2 sentences
- NO support tickets in sales mode
- Focus on moving toward booking

Be conversational."""
        
        conversation_messages.insert(0, {'role': 'system', 'content': prompt})
        
        # Single LLM call with functions
        logger.info("💬 Calling LLM...")
        
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
                llm_response = "Let me help you book that demo. What date works best?"
            
            else:
                # ✅ FIX: Update state BEFORE execution
                if function_name == 'check_slot_availability' and booking_session:
                    booking_orchestrator.transition_state(call_sid, BookingState.CHECKING_AVAILABILITY, "Verifying slot")
                
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
                
                logger.info(f"✓ Result: {llm_response[:80]}...")
                
                # ✅ FIX: Update state AFTER execution based on ACTUAL result
                if function_name == 'check_slot_availability':
                    # Check if slot is ACTUALLY available
                    if 'available' in llm_response.lower() and 'not available' not in llm_response.lower() and 'outside' not in llm_response.lower():
                        booking_orchestrator.update_session_data(call_sid, 'slot_available', True)
                        booking_orchestrator.transition_state(call_sid, BookingState.COLLECTING_EMAIL, "Slot confirmed")
                        logger.info("✅ Slot verified as available")
                    else:
                        logger.warning("⚠️ Slot NOT available - staying in collecting state")
                        booking_orchestrator.transition_state(call_sid, BookingState.COLLECTING_DATE, "Slot unavailable, need new date")
                
                elif function_name == 'verify_customer_email':
                    booking_orchestrator.update_session_data(call_sid, 'email_verified', True)
                    booking_orchestrator.transition_state(call_sid, BookingState.CONFIRMING_BOOKING, "Email verified")
                
                elif function_name == 'create_booking':
                    # ✅ FIX: Only transition if booking SUCCEEDED
                    if ('booking id' in llm_response.lower() or 'scheduled' in llm_response.lower()) and 'failed' not in llm_response.lower() and 'unavailable' not in llm_response.lower():
                        booking_orchestrator.transition_state(call_sid, BookingState.COMPLETED, "Booking complete!")
                        logger.info(f"✅ BOOKING COMPLETED for {customer_name}")
                    else:
                        # Booking failed - DON'T mark as completed
                        logger.error(f"❌ BOOKING FAILED: {llm_response[:100]}")
                        booking_orchestrator.transition_state(call_sid, BookingState.COLLECTING_DATE, "Booking failed, restart")
        
        else:
            llm_response = response.content
            logger.info(f"💬 Direct response: {llm_response[:80]}...")
        
        # Save to transcript
        conversation_transcript.append({
            'role': 'assistant',
            'content': llm_response,
            'timestamp': datetime.utcnow().isoformat(),
            'booking_mode': is_booking_mode,
            'slots_provided': bool(available_slots_text)
        })
        
        logger.info(f"🤖 AGENT: {llm_response[:100]}...")
        
        # Save to DB
        db.add(ConversationTurn(
            call_sid=call_sid,
            role="assistant",
            content=llm_response,
            created_at=datetime.utcnow()
        ))
        db.commit()
        
        # ✅ CRITICAL: Set speaking flag, timing will be calculated after actual chunks
        audio_timing_ref['speaking'] = True
        
        audio_task = asyncio.create_task(
            stream_elevenlabs_audio_with_playback_and_timing(
                websocket, stream_sid, llm_response, stop_audio_flag, audio_timing_ref
            )
        )
        current_audio_task_ref['task'] = audio_task
        
        try:
            await audio_task
            logger.info(f"✅ Audio completed")
        except asyncio.CancelledError:
            logger.info("Audio cancelled by interruption")
            raise
        finally:
            audio_timing_ref['speaking'] = False
            audio_timing_ref['expected_end'] = 0
            current_audio_task_ref['task'] = None
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        error_msg = "Could you repeat that?"
        
        audio_timing_ref['speaking'] = True
        audio_timing_ref['expected_end'] = time.time() + 2.0
        
        error_task = asyncio.create_task(
            stream_elevenlabs_audio_with_playback_and_timing(
                websocket, stream_sid, error_msg, stop_audio_flag, audio_timing_ref
            )
        )
        current_audio_task_ref['task'] = error_task
        
        try:
            await error_task
        except:
            pass
        finally:
            audio_timing_ref['speaking'] = False
            audio_timing_ref['expected_end'] = 0
            current_audio_task_ref['task'] = None


@router.post("/outbound-connect")
async def handle_outbound_connect(request: Request):
    """Outbound call handler"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        global from_number_global
        global to_number_global
        
        from_number_global = from_number
        to_number_global = to_number
        
        # Get parameters
        campaign_id = request.query_params.get("campaign_id")
        company_id = request.query_params.get("company_id")
        agent_id = request.query_params.get("agent_id")
        customer_name = request.query_params.get("customer_name", "")
        
        logger.info(f"📞 OUTBOUND CALL - CallSid: {call_sid}")
        logger.info(f"From: {from_number}, To: {to_number}")
        logger.info(f"Company: {company_id}, Agent: {agent_id}, Customer: {customer_name}")
        
        # Generate TwiML
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
        
        # ✅ CRITICAL FIX: ADD call_sid TO WEBSOCKET URL
        stream_url = f"wss://{ws_domain}/api/v1/twilio-elevenlabs/outbound-stream?call_sid={call_sid}"
        
        logger.info(f"Stream URL: {stream_url}")
        
        connect.stream(url=stream_url)
        response.append(connect)
        
        return Response(content=str(response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        response = VoiceResponse()
        response.say("An error occurred.", voice="Polly.Joanna")
        return Response(content=str(response), media_type="application/xml")


@router.websocket("/outbound-stream")
async def handle_outbound_stream(websocket: WebSocket):
    """Handle outbound call WebSocket stream"""
    await websocket.accept()
    logger.info("✅ WebSocket ACCEPTED")

    call_sid = None
    stream_sid = None
    company_id = None
    agent_id = None
    customer_name = None
    campaign_id = None
    session_id = None
    
    # Audio management
    current_audio_task = None
    current_audio_task_ref = {'task': None}
    stop_audio_flag = {'stop': False}
    audio_timing_ref = {'speaking': False, 'expected_end': 0}
    
    # Conversation state
    conversation_history = []
    greeting_sent = False
    greeting = None
    greeting_start_time = None
    company_name = None
    agent_name = None
    rejection_count = 0
    current_processing_task = None
    
    try:
        # ============================================
        # STEP 1: GET CALL SID FROM QUERY PARAMS
        # ============================================
        logger.info("🚀 Starting initialization...")
        
        # ✅ CRITICAL: Get call_sid from query params
        call_sid = websocket.query_params.get("call_sid")
        
        if not call_sid:
            logger.error("❌ No call_sid in query params")
            await websocket.close()
            return
        
        logger.info(f"📞 Call SID: {call_sid}")
        
        # Get context from stored data
        context = call_context.get(call_sid, {})
        company_id = context.get("company_id")
        agent_id = context.get("agent_id")
        customer_name = context.get("customer_name", "there")
        campaign_id = context.get("campaign_id", "UNKNOWN")
        call_type = context.get("call_type", "outgoing")
        
        if not all([company_id, agent_id]):
            logger.error(f"❌ Missing parameters - company_id: {company_id}, agent_id: {agent_id}")
            await websocket.close()
            return
        
        logger.info(f"✅ Parameters loaded - Company: {company_id}, Agent: {agent_id}, Customer: {customer_name}")
        
        # ============================================
        # STEP 2: PARALLEL INITIALIZATION
        # ============================================
        init_start = time.time()
        
        async def init_agent():
            agent_data = await agent_config_service.get_master_agent(company_id, agent_id)
            if not agent_data:
                raise ValueError(f"Agent {agent_id} not found")
            return agent_data
        
        async def init_company():
            comp_name = company_service.get_company_name_by_id(company_id)
            if not comp_name:
                raise ValueError(f"Company {company_id} not found")
            return comp_name
        
        master_agent, comp_name = await asyncio.gather(init_agent(), init_company())
        
        agent_name = master_agent.get("name", "AI Assistant")
        company_name = comp_name
        current_agent_context = master_agent
        
        additional_context = master_agent.get('additional_context', {})
        business_context = additional_context.get('businessContext', 'our services')
        
        greeting = (
            f"Hello {customer_name}! This is {agent_name} calling from {company_name}. "
            f"I'm reaching out because we offer {business_context}. "
            f"Would you be interested in learning more?"
        )
        
        init_duration = time.time() - init_start
        logger.info(f"⚡ Initialization complete in {init_duration:.2f}s")
        logger.info(f"💬 Greeting pre-generated: '{greeting[:50]}...'")
        
        call_metadata = {
            'start_time': datetime.utcnow(),
            'from_number': from_number_global,
            'to_number': to_number_global,
            'company_id': company_id,
            'call_type': call_type
        }
        
        available_agents = await agent_config_service.get_company_agents(company_id)
        specialized_agents = [a for a in available_agents if a['agent_id'] != agent_id]
        
        db = SessionLocal()
        deepgram_service = DeepgramWebSocketService()
        
        call_state = {"first_interaction": True, "interaction_count": 0}
        
        # ============================================
        # STEP 3: CALLBACKS
        # ============================================
        
        async def on_interim_transcript(session_id: str, transcript: str, confidence: float):
            nonlocal stop_audio_flag, current_audio_task, current_processing_task
            
            current_time = time.time()
            should_be_speaking = (
                audio_timing_ref['speaking'] or 
                (current_time < audio_timing_ref['expected_end'])
            )
            
            if not should_be_speaking:
                return
            
            word_count = len(transcript.split())
            if word_count >= 2:
                logger.warning(f"🚨 INTERRUPTION: '{transcript}' (conf: {confidence:.2f})")
                
                stop_audio_flag['stop'] = True
                
                if current_audio_task_ref['task'] and not current_audio_task_ref['task'].done():
                    current_audio_task_ref['task'].cancel()
                    try:
                        await current_audio_task_ref['task']
                    except asyncio.CancelledError:
                        pass
                
                if current_audio_task and not current_audio_task.done():
                    current_audio_task.cancel()
                
                if current_processing_task and not current_processing_task.done():
                    current_processing_task.cancel()
                    try:
                        await current_processing_task
                    except asyncio.CancelledError:
                        pass
                
                audio_timing_ref['speaking'] = False
                audio_timing_ref['expected_end'] = 0
                
                try:
                    await websocket.send_json({"event": "clear", "streamSid": stream_sid})
                except:
                    pass
                
                await asyncio.sleep(0.5)
                stop_audio_flag['stop'] = False
        
        async def on_deepgram_transcript(session_id: str, transcript: str):
            nonlocal conversation_history, current_agent_context, current_audio_task
            nonlocal current_processing_task, stop_audio_flag, greeting_start_time, rejection_count
            
            if not transcript or transcript.strip() == "":
                return
            
            if greeting_start_time:
                elapsed = (datetime.utcnow() - greeting_start_time).total_seconds()
                if elapsed < 3:
                    return
            
            logger.info(f"👤 CUSTOMER: '{transcript}'")
            
            intent_analysis = await intent_detection_service.detect_customer_intent(
                customer_message=transcript,
                conversation_history=conversation_history,
                call_type=call_type
            )
            
            conversation_history.append({
                "role": "user",
                "content": transcript,
                "timestamp": datetime.utcnow().isoformat(),
                'intent_type': intent_analysis.get('intent_type'),
                'sentiment': intent_analysis.get('sentiment'),
                'buying_readiness': intent_analysis.get('buying_readiness')
            })
            
            try:
                db.add(ConversationTurn(
                    call_sid=call_sid,
                    role="user",
                    content=transcript,
                    created_at=datetime.utcnow()
                ))
                db.commit()
            except:
                pass
            
            intent_type = intent_analysis.get('intent_type')
            
            if intent_type == 'rejection':
                rejection_count += 1
                if rejection_count >= 2:
                    farewell = f"I understand, {customer_name}. Thank you for your time. Have a wonderful day!"
                    conversation_history.append({
                        'role': 'assistant',
                        'content': farewell,
                        'timestamp': datetime.utcnow().isoformat(),
                        'call_ended': True
                    })
                    
                    audio_timing_ref['speaking'] = True
                    current_audio_task = asyncio.create_task(
                        stream_elevenlabs_audio_with_playback_and_timing(
                            websocket, stream_sid, farewell, stop_audio_flag, audio_timing_ref
                        )
                    )
                    current_audio_task_ref['task'] = current_audio_task
                    
                    try:
                        await current_audio_task
                    except:
                        pass
                    finally:
                        audio_timing_ref['speaking'] = False
                        audio_timing_ref['expected_end'] = 0
                        current_audio_task_ref['task'] = None
                    return
            else:
                if intent_type in ['soft_interest', 'strong_buying', 'question']:
                    rejection_count = 0
            
            call_state["interaction_count"] += 1
            
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
                    current_agent_id=agent_id,
                    company_id=company_id,
                    conversation_transcript=conversation_history,
                    intent_analysis=intent_analysis,
                    call_type=call_type,
                    audio_timing_ref=audio_timing_ref,
                    current_audio_task_ref=current_audio_task_ref
                )
            )
            
            try:
                await current_processing_task
            except asyncio.CancelledError:
                pass
            finally:
                current_audio_task = None
                current_processing_task = None
                current_audio_task_ref['task'] = None
        
        # ============================================
        # STEP 4: INIT DEEPGRAM (non-blocking)
        # ============================================
        session_id = f"deepgram_{call_sid}"
        logger.info(f"🎙️ Initializing Deepgram in background...")
        
        deepgram_task = asyncio.create_task(
            deepgram_service.initialize_session(
                session_id=session_id,
                callback=on_deepgram_transcript,
                interruption_callback=on_interim_transcript
            )
        )
        
        # ============================================
        # STEP 5: GREETING FLOW
        # ============================================
        
        # Wait for Twilio to send the "start" event with stream_sid
        logger.info("📡 Waiting for 'start' event...")
        
        for attempt in range(3):
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                data = json.loads(message)
                event_type = data.get("event")
                
                logger.info(f"📨 Event #{attempt + 1}: {event_type}")
                
                if event_type == "connected":
                    continue
                    
                elif event_type == "start":
                    stream_sid = data.get("streamSid")
                    logger.info(f"✅ Stream started: {stream_sid}")
                    break
                    
            except asyncio.TimeoutError:
                logger.error(f"⏱️ Timeout on attempt #{attempt + 1}")
                continue
        
        if not stream_sid:
            logger.error("❌ Could not get stream_sid")
            await websocket.close()
            return
        
        # Make sure Deepgram is ready
        if not deepgram_task.done():
            logger.info("⏳ Waiting for Deepgram...")
            await deepgram_task
        
        logger.info("✅ Deepgram ready")
        
        # Play greeting
        logger.info(f"🚀 INSTANT GREETING - Starting audio NOW")
        audio_timing_ref['speaking'] = True
        stop_audio_flag['stop'] = False
        greeting_start_time = datetime.utcnow()
        
        current_audio_task = asyncio.create_task(
            stream_elevenlabs_audio_with_playback_and_timing(
                websocket, stream_sid, greeting, stop_audio_flag, audio_timing_ref
            )
        )
        current_audio_task_ref['task'] = current_audio_task
        
        try:
            await current_audio_task
            logger.info("✅ Greeting completed")
        except asyncio.CancelledError:
            logger.info("⚠️ Greeting cancelled")
        except Exception as e:
            logger.error(f"❌ Greeting error: {e}")
        finally:
            audio_timing_ref['speaking'] = False
            audio_timing_ref['expected_end'] = 0
            current_audio_task = None
            current_audio_task_ref['task'] = None
        
        greeting_sent = True
        conversation_history.append({
            "role": "assistant",
            "content": greeting,
            "timestamp": greeting_start_time.isoformat()
        })
        await asyncio.sleep(0.5)
        
        # ============================================
        # STEP 6: MESSAGE LOOP
        # ============================================
        logger.info("📡 Entering message loop...")
        
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                data = json.loads(message)
                event = data.get("event")
                
                if event == "media":
                    payload = data.get("media", {}).get("payload")
                    if payload and session_id:
                        audio = await deepgram_service.convert_twilio_audio(payload, session_id)
                        if audio:
                            await deepgram_service.process_audio_chunk(session_id, audio)
                
                elif event == "stop":
                    logger.info("🛑 Stream stopped")
                    break
            
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                logger.info("🔌 WebSocket disconnected")
                break
            except Exception as e:
                logger.error(f"❌ Error in message loop: {e}")
                break
    
    except Exception as e:
        logger.error(f"❌ Fatal error in outbound stream: {e}")
        logger.exception(e)
    
    finally:
        logger.info(f"🧹 Cleanup for {call_sid}")
        
        if current_audio_task and not current_audio_task.done():
            current_audio_task.cancel()
        
        if session_id:
            try:
                await deepgram_service.close_session(session_id)
            except:
                pass
        
        if conversation_history and call_sid:
            try:
                call_duration = int((datetime.utcnow() - call_metadata['start_time']).total_seconds())
                
                s3_urls = await call_recording_service.save_call_data(
                    call_sid=call_sid,
                    company_id=company_id,
                    agent_id=agent_id,
                    transcript=conversation_history,
                    recording_url=None,
                    duration=call_duration,
                    from_number=call_metadata.get('from_number'),
                    to_number=call_metadata.get('to_number')
                )
                
                try:
                    call_record = db.query(Call).filter_by(call_sid=call_sid).first()
                    if call_record:
                        call_record.transcription = s3_urls.get('transcript_url')
                        call_record.duration = call_duration
                        call_record.status = 'completed'
                        call_record.ended_at = datetime.utcnow()
                        db.commit()
                except:
                    db.rollback()
            except:
                pass
        
        intent_router_service.clear_call(call_sid)
        call_context.pop(call_sid, None)
        db.close()
        
        try:
            await websocket.close()
        except:
            pass


@router.post("/initiate-outbound-call")
async def initiate_outbound_call(request: Request):
    """Initiate an outbound call"""
    try:
        data = await request.json()
        
        to_number = data.get("to_number")
        customer_name = data.get("customer_name", "")
        company_id = data.get("company_id")
        agent_id = data.get("agent_id")
        campaign_id = data.get("campaign_id", "")
        from_number = data.get("from_number", settings.twilio_phone_number)
        
        if not to_number or not company_id or not agent_id:
            return {"success": False, "error": "Missing required fields"}
        
        logger.info(f"📞 Initiating call to {to_number}")
        
        ws_domain = settings.base_url
        callback_url = (
            f"{ws_domain}/api/v1/twilio-elevenlabs/outbound-connect"
            f"?company_id={company_id}"
            f"&agent_id={agent_id}"
            f"&customer_name={quote(customer_name)}"
            f"&campaign_id={campaign_id}"
        )
        
        call = twilio_client.calls.create(
            to=to_number,
            from_=from_number,
            url=callback_url,
            method='POST',
            status_callback=f"{ws_domain}/api/v1/twilio-elevenlabs/call-status",
            status_callback_event=['initiated', 'ringing', 'answered', 'completed'],
            status_callback_method='POST',
            record=False,
            timeout=30,
            machine_detection='DetectMessageEnd',
            machine_detection_timeout=5
        )
        
        logger.info(f"✅ Call initiated: {call.sid}")
        
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
        except:
            db.rollback()
        finally:
            db.close()
        
        return {
            "success": True,
            "call_sid": call.sid,
            "to_number": to_number,
            "status": call.status
        }
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return {"success": False, "error": str(e)}
