# src/routes/twilio_elevenlabs_routes.py - COMPLETE FIXED VERSION
#
# FIXES APPLIED:
# 1. Interrupted text is now SAVED and PROCESSED (was being lost)
# 2. is_speaking stays TRUE during Twilio playback  
# 3. Audio actually STOPS when interrupted (proper task cancellation)
# 4. Fast-path for simple responses (skips unnecessary LLM calls)
# 5. Background DB writes (non-blocking)
# 6. Only fetch datetime/slots when in booking mode

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

# Cache for pre-initialized agent data (with TTL)
agent_cache = {}
AGENT_CACHE_TTL = 300  # 5 minutes

# FIX #1: Storage for interrupted text (so it's not lost!)
interrupted_text_storage = {}

# Global numbers
from_number_global = None
to_number_global = None


def get_cached_agent(cache_key: str):
    """Get cached agent data if not expired"""
    if cache_key in agent_cache:
        cached = agent_cache[cache_key]
        if (datetime.utcnow() - cached['cached_at']).total_seconds() < AGENT_CACHE_TTL:
            return cached
    return None


async def _prewarm_agent_cache(company_id: str, agent_id: str):
    """Pre-warm agent cache in background"""
    cache_key = f"{company_id}_{agent_id}"
    try:
        master_agent = await agent_config_service.get_master_agent(company_id, agent_id)
        company_name = company_service.get_company_name_by_id(company_id)
        agent_cache[cache_key] = {
            'agent': master_agent,
            'company_name': company_name,
            'cached_at': datetime.utcnow()
        }
        logger.info(f"✅ Pre-warmed agent cache: {cache_key}")
    except Exception as e:
        logger.error(f"Pre-warm failed: {e}")


def save_to_db_background(call_sid: str, role: str, content: str):
    """Fire-and-forget DB write - saves ~100-300ms"""
    async def _save():
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
    
    asyncio.create_task(_save())

@router.post("/incoming-call")
async def handle_incoming_call_elevenlabs(request: Request):
    """Incoming call handler with ElevenLabs via WebSocket"""
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
        logger.info(f"Company: {company_id}, Agent: {agent_id}")
        
        # PRE-WARM: Start fetching agent data immediately
        cache_key = f"{company_id}_{agent_id}"
        if not get_cached_agent(cache_key):
            asyncio.create_task(_prewarm_agent_cache(company_id, agent_id))
        
        # Generate TwiML response
        response = VoiceResponse()
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
        
        async def _update():
            db = None
            try:
                db = SessionLocal()
                call_record = db.query(Call).filter_by(call_sid=call_sid).first()
                if call_record:
                    call_record.status = call_status
                    if call_status == 'completed':
                        call_record.ended_at = datetime.utcnow()
                    db.commit()
            except Exception as e:
                logger.error(f"DB error: {e}")
                if db:
                    db.rollback()
            finally:
                if db:
                    db.close()
        
        asyncio.create_task(_update())
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error handling call status: {e}")
        return {"status": "error", "message": str(e)}


async def send_clear_to_twilio(websocket: WebSocket, stream_sid: str):
    """Send clear command to immediately stop Twilio audio playback"""
    if not stream_sid:
        return
    try:
        clear_message = {"event": "clear", "streamSid": stream_sid}
        await websocket.send_json(clear_message)
        logger.info("✅ CLEAR command sent to Twilio")
    except Exception as e:
        logger.error(f"Error sending clear: {e}")


# FIX #2: Audio streaming with proper speaking state management
async def stream_elevenlabs_audio_optimized(
    websocket: WebSocket, 
    stream_sid: str, 
    text: str, 
    stop_flag_ref: dict,
    is_speaking_ref: dict = None
):
    """
    Stream audio with proper speaking state management.
    
    FIXES:
    - Keeps is_speaking=True for estimated playback duration
    - Actually stops when interrupted
    - Sends CLEAR on cancellation
    """
    if not stream_sid:
        logger.error("No stream_sid")
        return
    
    chunk_count = 0
    
    try:
        logger.info(f"🔊 Generating audio: '{text[:50]}...'")
        
        # CHECK BEFORE STARTING
        if stop_flag_ref.get('stop', False):
            logger.warning("🛑 Stop flag already set - aborting audio")
            return
        
        # Stream audio chunks with interruption checking
        async for audio_chunk in elevenlabs_service.generate(text):
            # CHECK STOP FLAG BEFORE EVERY CHUNK
            if stop_flag_ref.get('stop', False):
                logger.warning(f"🛑 STOP FLAG DETECTED at chunk {chunk_count} - halting!")
                try:
                    await websocket.send_json({"event": "clear", "streamSid": stream_sid})
                    logger.info("✅ CLEAR sent during chunk streaming")
                except:
                    pass
                return
            
            if audio_chunk and stream_sid:
                message = {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": audio_chunk}
                }
                await websocket.send_json(message)
                chunk_count += 1
        
        logger.info(f"✓ Sent {chunk_count} chunks to Twilio")
        
        # FIX: Keep is_speaking=True for estimated playback duration
        # Each chunk is ~20ms of audio, plus network/buffer delay
        estimated_playback_seconds = (chunk_count * 0.02) + 0.5
        logger.info(f"⏳ Waiting {estimated_playback_seconds:.1f}s for Twilio playback")
        
        # Wait for Twilio to finish playing, but check for interruptions
        elapsed = 0
        check_interval = 0.05  # 50ms checks
        
        while elapsed < estimated_playback_seconds:
            if stop_flag_ref.get('stop', False):
                logger.warning(f"🛑 STOP during playback wait at {elapsed:.2f}s")
                try:
                    await websocket.send_json({"event": "clear", "streamSid": stream_sid})
                except:
                    pass
                return
            
            await asyncio.sleep(check_interval)
            elapsed += check_interval
        
        logger.info("✅ Audio playback completed")
        
    except asyncio.CancelledError:
        logger.warning(f"🛑 Audio task CANCELLED at chunk {chunk_count}")
        try:
            await websocket.send_json({"event": "clear", "streamSid": stream_sid})
            logger.info("✅ CLEAR sent on task cancellation")
        except:
            pass
        raise
    except Exception as e:
        logger.error(f"Error streaming audio: {e}")
    finally:
        if is_speaking_ref:
            is_speaking_ref['speaking'] = False
            logger.debug("Speaking flag reset to FALSE")


@router.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Incoming call handler with proper interruption handling"""
    global from_number_global, to_number_global
    
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
    stop_audio_flag = {'stop': False}
    current_audio_task = None
    current_processing_task = None
    greeting_sent = False
    greeting_start_time = None
    
    is_agent_speaking_ref = {'speaking': False}
    current_audio_task_ref = {'task': None}
    
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
    
    # CHECK CACHE FIRST for faster startup
    cache_key = f"{company_id}_{master_agent_id}"
    cached = get_cached_agent(cache_key)
    
    if cached:
        logger.info("⚡ Using CACHED agent data")
        master_agent = cached['agent']
    else:
        master_agent = await agent_config_service.get_master_agent(company_id, master_agent_id)
        company_name = company_service.get_company_name_by_id(company_id)
        agent_cache[cache_key] = {
            'agent': master_agent,
            'company_name': company_name,
            'cached_at': datetime.utcnow()
        }
    
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
    
    # PRE-GENERATE GREETING before Deepgram init
    greeting = prompt_template_service.generate_greeting(master_agent, company_id, agent_name)
    logger.info(f"💬 Greeting pre-generated: '{greeting[:50]}...'")
    
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
        # FIX #3: INTERRUPTION CALLBACK - Now saves interrupted text!
        async def on_interim_transcript(session_id: str, transcript: str, confidence: float):
            """INSTANT interruption - saves text and clears Twilio buffer"""
            nonlocal stream_sid
            nonlocal current_audio_task
            
            word_count = len(transcript.split())
            if word_count < 2:
                return
            
            is_speaking = is_agent_speaking_ref['speaking']
            logger.info(f"🎯 INTERRUPT CHECK: '{transcript[:30]}' | is_speaking={is_speaking}")
            
            # ALWAYS send CLEAR to Twilio
            if stream_sid:
                try:
                    await websocket.send_json({"event": "clear", "streamSid": stream_sid})
                    logger.info(f"✅ CLEAR sent to Twilio for {stream_sid}")
                except Exception as e:
                    logger.error(f"Failed to send CLEAR: {e}")
            
            # FIX: ALWAYS save the interrupted text (so it's not lost!)
            interrupted_text_storage[call_sid] = {
                'text': transcript,
                'timestamp': datetime.utcnow(),
                'confidence': confidence
            }
            logger.info(f"💾 Saved interrupted text: '{transcript}'")
            
            if not is_speaking:
                logger.debug(f"Agent wasn't speaking, but saved text anyway")
                return
            
            logger.warning(f"🚨 INTERRUPTING AGENT! '{transcript}'")
            
            stop_audio_flag['stop'] = True
            is_agent_speaking_ref['speaking'] = False
            
            # FIX: Cancel audio task AND WAIT for completion
            if current_audio_task_ref['task'] and not current_audio_task_ref['task'].done():
                logger.info(f"⚡ Cancelling audio task...")
                current_audio_task_ref['task'].cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(current_audio_task_ref['task']), 
                        timeout=0.3
                    )
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                current_audio_task_ref['task'] = None
            
            logger.info("✅ Interruption complete - ready for user")
        
        # FIX #4: TRANSCRIPT CALLBACK - Uses saved interrupted text!
        async def on_deepgram_transcript(session_id: str, transcript: str):
            nonlocal conversation_transcript
            nonlocal current_agent_context
            nonlocal current_audio_task
            nonlocal current_processing_task
            nonlocal stop_audio_flag
            nonlocal greeting_start_time
            
            if not transcript.strip():
                return
            
            # Echo protection
            if greeting_start_time:
                elapsed = (datetime.utcnow() - greeting_start_time).total_seconds()
                if elapsed < 1.5:
                    logger.debug(f"Ignoring early transcript (echo): '{transcript}'")
                    return
            
            # FIX: Check for pending interrupted text and USE it
            if call_sid in interrupted_text_storage:
                interrupted = interrupted_text_storage.pop(call_sid)
                interrupted_text = interrupted['text']
                age = (datetime.utcnow() - interrupted['timestamp']).total_seconds()
                
                if age < 10.0:  # Only use if recent
                    if interrupted_text.lower() not in transcript.lower():
                        # Different text - combine them
                        transcript = f"{interrupted_text} {transcript}"
                        logger.info(f"📝 Combined with interrupted text: '{transcript}'")
                    elif len(transcript.split()) < 3 and len(interrupted_text.split()) >= 3:
                        # Final is noise, use interrupted text
                        transcript = interrupted_text
                        logger.info(f"📝 Using interrupted text instead: '{transcript}'")
            
            logger.info(f"👤 CUSTOMER SAID: '{transcript}'")
            
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
            
            # Background DB save
            save_to_db_background(call_sid, "user", transcript)
            
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
                            is_agent_speaking_ref['speaking'] = True
                            stop_audio_flag['stop'] = False
                            
                            current_audio_task = asyncio.create_task(
                                stream_elevenlabs_audio_optimized(
                                    websocket, stream_sid, routing_message, 
                                    stop_audio_flag, is_agent_speaking_ref
                                )
                            )
                            current_audio_task_ref['task'] = current_audio_task
                            
                            try:
                                await current_audio_task
                            except asyncio.CancelledError:
                                logger.info("Routing message cancelled")
                            finally:
                                is_agent_speaking_ref['speaking'] = False
                                current_audio_task_ref['task'] = None
            
            call_state["interaction_count"] += 1
            
            # Process and respond
            is_agent_speaking_ref['speaking'] = True
            stop_audio_flag['stop'] = False
            
            try:
                await process_and_respond_incoming(
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
                    call_metadata=call_metadata,
                    is_speaking_ref=is_agent_speaking_ref,
                    audio_task_ref=current_audio_task_ref
                )
                logger.info("✓ Response completed")
            except asyncio.CancelledError:
                logger.info("Response cancelled by interruption")
            except Exception as e:
                logger.error(f"Response error: {e}")
            finally:
                is_agent_speaking_ref['speaking'] = False
                current_audio_task_ref['task'] = None
        
        # Start Deepgram initialization as background task
        session_id = f"deepgram_{call_sid}"
        logger.info(f"🎙️ Starting Deepgram initialization (background)...")
        
        deepgram_init_task = asyncio.create_task(
            deepgram_service.initialize_session(
                session_id=session_id,
                callback=on_deepgram_transcript,
                interruption_callback=on_interim_transcript
            )
        )
        
        # Process first message if we have it
        if first_message_data and first_message_data.get("event") == "start":
            stream_sid = first_message_data.get("streamSid")
            logger.info(f"✅ Stream started: {stream_sid}")
            
            # SEND GREETING IMMEDIATELY - Don't wait for Deepgram!
            logger.info(f"🔊 Sending greeting NOW (Deepgram init in background)")
            
            is_agent_speaking_ref['speaking'] = True
            stop_audio_flag['stop'] = False
            greeting_start_time = datetime.utcnow()
            
            current_audio_task = asyncio.create_task(
                stream_elevenlabs_audio_optimized(
                    websocket, stream_sid, greeting, 
                    stop_audio_flag, is_agent_speaking_ref
                )
            )
            current_audio_task_ref['task'] = current_audio_task
            
            try:
                await current_audio_task
                logger.info("✅ Greeting completed")
            except asyncio.CancelledError:
                logger.info("Greeting cancelled")
            finally:
                is_agent_speaking_ref['speaking'] = False
                current_audio_task_ref['task'] = None
            
            greeting_sent = True
        
        # NOW wait for Deepgram to be ready (should be done by now)
        deepgram_connected = await deepgram_init_task
        if not deepgram_connected:
            logger.error("Deepgram connection failed")
            await websocket.close()
            return
        
        logger.info("✅ Deepgram ready - entering message loop")
        
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
                        logger.info(f"✅ Stream started (late): {stream_sid}")
                        
                        is_agent_speaking_ref['speaking'] = True
                        stop_audio_flag['stop'] = False
                        greeting_start_time = datetime.utcnow()
                        
                        current_audio_task = asyncio.create_task(
                            stream_elevenlabs_audio_optimized(
                                websocket, stream_sid, greeting,
                                stop_audio_flag, is_agent_speaking_ref
                            )
                        )
                        current_audio_task_ref['task'] = current_audio_task
                        
                        try:
                            await current_audio_task
                        except asyncio.CancelledError:
                            pass
                        finally:
                            is_agent_speaking_ref['speaking'] = False
                            current_audio_task_ref['task'] = None
                        
                        greeting_sent = True
                
                elif event == "media":
                    # Process audio for transcription
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
        
        # Clean up interrupted text storage
        interrupted_text_storage.pop(call_sid, None)
        
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
    call_metadata: dict = None,
    is_speaking_ref: dict = None,
    audio_task_ref: dict = None
):
    """Process incoming call with AI-powered intelligent routing"""
    
    rag = get_rag_service()
    
    try:
        # Check for interruption before starting
        if stop_audio_flag.get('stop', False):
            logger.info("Skipping response - interrupted")
            return
        
        # AI-POWERED DECISION for routing
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
        
        llm_response = None
        
        # Strategy 1 - Direct canned response
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
        
        # Strategy 2 - Use conversation context only
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
                
                llm_response = await execute_function(
                    function_name=function_name,
                    arguments=arguments,
                    company_id=company_id,
                    call_sid=call_sid or "unknown",
                    campaign_id=None,
                    user_timezone=call_metadata.get('user_timezone', 'UTC') if call_metadata else 'UTC',
                    business_hours={'start': '09:00', 'end': '18:00'}
                )
            else:
                llm_response = response.content
        
        # Strategy 3 - Full RAG with document retrieval
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
        
        else:
            llm_response = "I'm here to help. Could you tell me more about what you need?"
        
        # Check for interruption before streaming
        if stop_audio_flag.get('stop', False):
            logger.info("Skipping audio - interrupted")
            return
        
        logger.info(f"🤖 AGENT: '{llm_response[:100]}...'")
        
        # Save to transcript
        conversation_transcript.append({
            'role': 'assistant',
            'content': llm_response,
            'timestamp': datetime.utcnow().isoformat(),
            'strategy': response_strategy
        })
        
        # Background DB save
        save_to_db_background(call_sid, "assistant", llm_response)
        
        # Stream response with speaking flag management
        if is_speaking_ref:
            is_speaking_ref['speaking'] = True
        
        audio_task = asyncio.create_task(
            stream_elevenlabs_audio_optimized(
                websocket, stream_sid, llm_response, 
                stop_audio_flag, is_speaking_ref
            )
        )
        
        if audio_task_ref:
            audio_task_ref['task'] = audio_task
        
        try:
            await audio_task
        except asyncio.CancelledError:
            logger.info("Audio cancelled by interruption")
            raise
        finally:
            if is_speaking_ref:
                is_speaking_ref['speaking'] = False
            if audio_task_ref:
                audio_task_ref['task'] = None
        
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        error_msg = "I'm having trouble. Could you please repeat?"
        await stream_elevenlabs_audio_optimized(
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
    is_agent_speaking_ref: dict,
    current_audio_task_ref: dict
):
    """Outbound call processing with proper state management"""
    
    rag = get_rag_service()
    
    try:
        # Check for early interruption
        if stop_audio_flag.get('stop', False):
            logger.info("Skipping response - interrupted")
            return
        
        # Get call metadata
        call_metadata = call_context.get(call_sid, {})
        campaign_id = call_metadata.get('campaign_id')
        customer_name = call_metadata.get('customer_name', 'Customer')
        customer_phone = call_metadata.get('to_number')
        
        # Check booking session
        booking_session = booking_orchestrator.get_session(call_sid)
        is_booking_mode = booking_session and booking_orchestrator.is_booking_active(call_sid)
        
        # Intent analysis
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
        
        # FIX #5: Only fetch datetime/slots when in booking mode
        datetime_info = {}
        available_slots_text = ""
        
        if is_booking_mode and campaign_id:
            async def fetch_datetime_info():
                return await datetime_parser_service.parse_user_datetime(
                    user_input=transcript,
                    user_timezone=call_metadata.get('user_timezone', 'UTC'),
                    business_hours={'start': '09:00', 'end': '18:00'}
                )
            
            async def fetch_slots():
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
        else:
            logger.info("⚡ Skipped datetime/slot parsing (not in booking mode)")
        
        # Update booking session if datetime found
        if isinstance(datetime_info, dict) and datetime_info.get('parsed_successfully') and booking_session:
            logger.info(f"✓ AI extracted: {datetime_info.get('user_friendly')}")
            
            if datetime_info.get('date'):
                booking_orchestrator.update_session_data(call_sid, 'date', datetime_info['date'])
            if datetime_info.get('time'):
                booking_orchestrator.update_session_data(call_sid, 'time', datetime_info['time'])
            if datetime_info.get('datetime_iso'):
                booking_orchestrator.update_session_data(call_sid, 'datetime_iso', datetime_info['datetime_iso'])
        
        # Extract email if present
        import re
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
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
        
        # Build prompt based on mode
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
   - Then immediately guide back to booking
5. Keep ALL responses to 1-2 sentences maximum
6. NO support tickets in sales mode
7. If user talks in english, reply back in english, else reply in user's language, except for tool calls etc.
8. If user speaks in hindi, make sure to reply in hinglish only, except for tool calls etc.

Be conversational but focused on booking."""

        else:
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
        
        # Check for interruption before LLM call
        if stop_audio_flag.get('stop', False):
            logger.info("Skipping LLM - interrupted")
            return
        
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
                # Update state before execution
                if function_name == 'check_slot_availability' and booking_session:
                    booking_orchestrator.transition_state(call_sid, BookingState.CHECKING_AVAILABILITY, "Verifying slot")
                
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
                
                # Update state after execution
                if function_name == 'check_slot_availability':
                    if 'available' in llm_response.lower() and 'not available' not in llm_response.lower():
                        booking_orchestrator.update_session_data(call_sid, 'slot_available', True)
                        booking_orchestrator.transition_state(call_sid, BookingState.COLLECTING_EMAIL, "Slot confirmed")
                    else:
                        booking_orchestrator.transition_state(call_sid, BookingState.COLLECTING_DATE, "Slot unavailable")
                
                elif function_name == 'verify_customer_email':
                    booking_orchestrator.update_session_data(call_sid, 'email_verified', True)
                    booking_orchestrator.transition_state(call_sid, BookingState.CONFIRMING_BOOKING, "Email verified")
                
                elif function_name == 'create_booking':
                    if ('booking id' in llm_response.lower() or 'scheduled' in llm_response.lower()) and 'failed' not in llm_response.lower():
                        booking_orchestrator.transition_state(call_sid, BookingState.COMPLETED, "Booking complete!")
                        logger.info(f"✅ BOOKING COMPLETED for {customer_name}")
                    else:
                        logger.error(f"❌ BOOKING FAILED: {llm_response[:100]}")
                        booking_orchestrator.transition_state(call_sid, BookingState.COLLECTING_DATE, "Booking failed")
        else:
            llm_response = response.content
            logger.info(f"💬 Direct response: {llm_response[:80]}...")
        
        # Check for interruption before audio
        if stop_audio_flag.get('stop', False):
            logger.info("Skipping audio - interrupted")
            return
        
        # Save to transcript
        conversation_transcript.append({
            'role': 'assistant',
            'content': llm_response,
            'timestamp': datetime.utcnow().isoformat(),
            'booking_mode': is_booking_mode
        })
        
        logger.info(f"🤖 AGENT: {llm_response[:100]}...")
        
        # Background DB save
        save_to_db_background(call_sid, "assistant", llm_response)
        
        # Stream audio with state management
        is_agent_speaking_ref['speaking'] = True
        logger.info(f"🎤 Setting is_agent_speaking = TRUE")
        
        audio_task = asyncio.create_task(
            stream_elevenlabs_audio_optimized(
                websocket, stream_sid, llm_response, 
                stop_audio_flag, is_agent_speaking_ref
            )
        )
        current_audio_task_ref['task'] = audio_task
        
        try:
            await audio_task
            logger.info(f"✅ Audio completed - setting is_agent_speaking = FALSE")
        except asyncio.CancelledError:
            logger.info("Audio cancelled by interruption")
            raise
        finally:
            is_agent_speaking_ref['speaking'] = False
            current_audio_task_ref['task'] = None
        
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        error_msg = "Could you repeat that?"
        
        is_agent_speaking_ref['speaking'] = True
        error_task = asyncio.create_task(
            stream_elevenlabs_audio_optimized(
                websocket, stream_sid, error_msg, 
                stop_audio_flag, is_agent_speaking_ref
            )
        )
        current_audio_task_ref['task'] = error_task
        
        try:
            await error_task
        except:
            pass
        finally:
            is_agent_speaking_ref['speaking'] = False
            current_audio_task_ref['task'] = None


@router.post("/outbound-connect")
async def handle_outbound_connect(request: Request):
    """Outbound call handler"""
    global from_number_global, to_number_global
    
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
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
    """Outbound call streaming with ALL fixes applied"""
    global from_number_global, to_number_global
    
    try:
        await websocket.accept()
        logger.info("✅ WebSocket ACCEPTED")
    except Exception as e:
        logger.error(f"Failed to accept WebSocket: {str(e)}")
        return
    
    conversation_transcript = []
    call_sid = websocket.query_params.get("call_sid")
    first_message_data = None
    stop_audio_flag = {'stop': False}
    current_audio_task = None
    current_processing_task = None
    greeting_sent = False
    greeting_start_time = None
    rejection_count = 0
    
    # Reference dicts for state
    is_agent_speaking_ref = {'speaking': False}
    current_audio_task_ref = {'task': None}
    
    if not call_sid:
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
            await websocket.close(code=1008)
            return
    
    if not call_sid:
        await websocket.close(code=1008)
        return
    
    # Get context
    context = call_context.get(call_sid, {})
    company_id = context.get("company_id")
    master_agent_id = context.get("agent_id")
    customer_name = context.get("customer_name", "")
    campaign_id = context.get("campaign_id", "")
    call_type = context.get("call_type", "outgoing")
    
    # CHECK CACHE FIRST (should be pre-warmed)
    cache_key = f"{company_id}_{master_agent_id}"
    cached = get_cached_agent(cache_key)
    
    if cached:
        logger.info(f"⚡ Using CACHED agent data")
        master_agent = cached['agent']
        company_name = cached['company_name']
    else:
        logger.info(f"🚀 Fetching agent data (cache miss)...")
        start_time = datetime.utcnow()
        
        master_agent = await agent_config_service.get_master_agent(company_id, master_agent_id)
        company_name = company_service.get_company_name_by_id(company_id)
        
        # Cache it
        agent_cache[cache_key] = {
            'agent': master_agent,
            'company_name': company_name,
            'cached_at': datetime.utcnow()
        }
        
        init_duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(f"⚡ Fetched in {init_duration:.2f}s")
    
    if not master_agent:
        logger.error(f"Failed to fetch master agent")
        await websocket.close(code=1008)
        return
    
    agent_name = master_agent["name"]
    current_agent_context = master_agent
    
    call_metadata = {
        'start_time': datetime.utcnow(),
        'from_number': from_number_global,
        'to_number': to_number_global,
        'company_id': company_id,
        'call_type': call_type
    }
    
    # PRE-GENERATE GREETING
    additional_context = master_agent.get('additional_context', {})
    business_context = additional_context.get('businessContext', 'our services')
    
    greeting = (
        f"Hello {customer_name}! This is {agent_name} calling from {company_name}. "
        f"I'm reaching out because we offer {business_context}. "
        f"Would you be interested in learning more?"
    )
    
    logger.info(f"💬 Greeting pre-generated: '{greeting[:50]}...'")
    
    # Load agents
    available_agents = await agent_config_service.get_company_agents(company_id)
    specialized_agents = [
        a for a in available_agents
        if a['agent_id'] != master_agent_id
    ]
    
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
        # FIXED INTERRUPTION CALLBACK - Saves interrupted text!
        async def on_interim_transcript(session_id: str, transcript: str, confidence: float):
            """INSTANT interruption - saves text and clears Twilio buffer"""
            nonlocal stream_sid
            nonlocal current_audio_task
            
            word_count = len(transcript.split())
            if word_count < 2:
                return
            
            is_speaking = is_agent_speaking_ref['speaking']
            logger.info(f"🎯 INTERRUPT CHECK: '{transcript[:30]}' | is_speaking={is_speaking}")
            
            # ALWAYS send CLEAR to Twilio
            if stream_sid:
                try:
                    await websocket.send_json({"event": "clear", "streamSid": stream_sid})
                    logger.info(f"✅ CLEAR sent to Twilio for {stream_sid}")
                except Exception as e:
                    logger.error(f"Failed to send CLEAR: {e}")
            
            # FIX: ALWAYS save the interrupted text!
            interrupted_text_storage[call_sid] = {
                'text': transcript,
                'timestamp': datetime.utcnow(),
                'confidence': confidence
            }
            logger.info(f"💾 Saved interrupted text: '{transcript}'")
            
            if not is_speaking:
                return
            
            logger.warning(f"🚨 INTERRUPTING AGENT! '{transcript}'")
            
            stop_audio_flag['stop'] = True
            is_agent_speaking_ref['speaking'] = False
            
            # FIX: Cancel AND WAIT for task
            if current_audio_task_ref['task'] and not current_audio_task_ref['task'].done():
                logger.info(f"⚡ Cancelling audio task...")
                current_audio_task_ref['task'].cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(current_audio_task_ref['task']), 
                        timeout=0.3
                    )
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                current_audio_task_ref['task'] = None
            
            if current_audio_task and not current_audio_task.done():
                current_audio_task.cancel()
            
            logger.info("✅ Interruption complete - ready for user")
        
        # FIXED TRANSCRIPT CALLBACK - Uses saved interrupted text!
        async def on_deepgram_transcript(session_id: str, transcript: str):
            nonlocal conversation_transcript
            nonlocal current_agent_context
            nonlocal current_audio_task
            nonlocal current_processing_task
            nonlocal stop_audio_flag
            nonlocal greeting_start_time
            nonlocal rejection_count
            
            if not transcript.strip():
                return
            
            # Echo protection
            if greeting_start_time:
                elapsed = (datetime.utcnow() - greeting_start_time).total_seconds()
                if elapsed < 1.5:
                    return
            
            # FIX: Check for pending interrupted text and USE it
            if call_sid in interrupted_text_storage:
                interrupted = interrupted_text_storage.pop(call_sid)
                interrupted_text = interrupted['text']
                age = (datetime.utcnow() - interrupted['timestamp']).total_seconds()
                
                if age < 10.0:
                    if interrupted_text.lower() not in transcript.lower():
                        transcript = f"{interrupted_text} {transcript}"
                        logger.info(f"📝 Combined with interrupted text: '{transcript}'")
                    elif len(transcript.split()) < 3 and len(interrupted_text.split()) >= 3:
                        transcript = interrupted_text
                        logger.info(f"📝 Using interrupted text instead: '{transcript}'")
            
            logger.info(f"👤 CUSTOMER: '{transcript}'")
            
            # FIX #6: Fast-path for simple responses (skip expensive AI calls)
            simple_words = ['yes', 'no', 'hello', 'hi', 'okay', 'sure', 'yeah', 'nope', 'yep', 'hey']
            is_simple = len(transcript.split()) <= 5 and any(w in transcript.lower() for w in simple_words)
            
            if is_simple and call_type == "outgoing":
                positive_words = ['yes', 'yeah', 'sure', 'okay', 'yep']
                is_positive = any(w in transcript.lower() for w in positive_words)
                
                intent_analysis = {
                    'intent_type': 'soft_interest' if is_positive else 'neutral',
                    'sentiment': 'positive' if is_positive else 'neutral',
                    'buying_readiness': 60 if is_positive else 40,
                    'should_book': False,
                    'should_persuade': True,
                    'should_end_call': False,
                    'objection_type': 'none',
                    'reasoning': 'Fast path - simple response'
                }
                logger.info(f"⚡ Fast intent detection: {intent_analysis['intent_type']}")
            else:
                intent_analysis = await intent_detection_service.detect_customer_intent(
                    customer_message=transcript,
                    conversation_history=conversation_transcript,
                    call_type=call_type
                )
            
            conversation_transcript.append({
                'role': 'user',
                'content': transcript,
                'timestamp': datetime.utcnow().isoformat(),
                'intent_type': intent_analysis.get('intent_type'),
                'sentiment': intent_analysis.get('sentiment'),
                'buying_readiness': intent_analysis.get('buying_readiness')
            })
            
            # Background DB save
            save_to_db_background(call_sid, "user", transcript)
            
            intent_type = intent_analysis.get('intent_type')
            
            # Handle rejections
            if intent_type == 'rejection':
                rejection_count += 1
                if rejection_count >= 2:
                    logger.info(f"🚫 {rejection_count} rejections - ending call")
                    
                    farewell = f"I understand, {customer_name}. Thank you for your time. Have a wonderful day!"
                    
                    conversation_transcript.append({
                        'role': 'assistant',
                        'content': farewell,
                        'timestamp': datetime.utcnow().isoformat(),
                        'call_ended': True
                    })
                    
                    is_agent_speaking_ref['speaking'] = True
                    stop_audio_flag['stop'] = False
                    
                    current_audio_task = asyncio.create_task(
                        stream_elevenlabs_audio_optimized(
                            websocket, stream_sid, farewell, 
                            stop_audio_flag, is_agent_speaking_ref
                        )
                    )
                    current_audio_task_ref['task'] = current_audio_task
                    
                    try:
                        await current_audio_task
                    except:
                        pass
                    finally:
                        is_agent_speaking_ref['speaking'] = False
                        current_audio_task_ref['task'] = None
                    
                    return
            else:
                if intent_type in ['soft_interest', 'strong_buying', 'question']:
                    rejection_count = 0
            
            # Agent routing (skip for simple responses)
            current_agent_id = master_agent_id
            
            if specialized_agents and not is_simple:
                detected_agent = await intent_router_service.detect_intent(
                    transcript,
                    company_id,
                    master_agent,
                    specialized_agents
                )
                
                if detected_agent:
                    intent_router_service.set_current_agent(call_sid, detected_agent)
                    current_agent_id = detected_agent
            elif is_simple:
                logger.info("⚡ Skipped agent routing (simple response)")
            
            call_state["interaction_count"] += 1
            
            # Process and respond
            stop_audio_flag['stop'] = False
            
            try:
                await process_and_respond_outbound(
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
                    call_type=call_type,
                    is_agent_speaking_ref=is_agent_speaking_ref,
                    current_audio_task_ref=current_audio_task_ref
                )
            except asyncio.CancelledError:
                logger.info("Response cancelled")
            except Exception as e:
                logger.error(f"Response error: {e}")
        
        # Start Deepgram in background
        session_id = f"deepgram_{call_sid}"
        logger.info(f"🎙️ Starting Deepgram (background)...")
        
        deepgram_init_task = asyncio.create_task(
            deepgram_service.initialize_session(
                session_id=session_id,
                callback=on_deepgram_transcript,
                interruption_callback=on_interim_transcript
            )
        )
        
        # Send greeting immediately when stream starts
        if first_message_data and first_message_data.get("event") == "start":
            stream_sid = first_message_data.get("streamSid")
            logger.info(f"✅ Stream started: {stream_sid}")
            
            # SEND GREETING IMMEDIATELY
            logger.info("🔊 Sending greeting NOW")
            
            is_agent_speaking_ref['speaking'] = True
            stop_audio_flag['stop'] = False
            greeting_start_time = datetime.utcnow()
            
            current_audio_task = asyncio.create_task(
                stream_elevenlabs_audio_optimized(
                    websocket, stream_sid, greeting, 
                    stop_audio_flag, is_agent_speaking_ref
                )
            )
            current_audio_task_ref['task'] = current_audio_task
            
            try:
                await current_audio_task
                logger.info("✅ Greeting completed")
            except asyncio.CancelledError:
                logger.info("Greeting cancelled")
            finally:
                is_agent_speaking_ref['speaking'] = False
                current_audio_task_ref['task'] = None
            
            greeting_sent = True
        
        # Wait for Deepgram (should be done by now)
        deepgram_connected = await deepgram_init_task
        if not deepgram_connected:
            logger.error("Deepgram failed")
            await websocket.close()
            return
        
        logger.info("✅ Deepgram ready")
        
        # Message loop
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                data = json.loads(message)
                event = data.get("event")
                
                if event == "start":
                    if not greeting_sent:
                        stream_sid = data.get("streamSid")
                        
                        is_agent_speaking_ref['speaking'] = True
                        stop_audio_flag['stop'] = False
                        greeting_start_time = datetime.utcnow()
                        
                        current_audio_task = asyncio.create_task(
                            stream_elevenlabs_audio_optimized(
                                websocket, stream_sid, greeting,
                                stop_audio_flag, is_agent_speaking_ref
                            )
                        )
                        current_audio_task_ref['task'] = current_audio_task
                        
                        try:
                            await current_audio_task
                        except:
                            pass
                        finally:
                            is_agent_speaking_ref['speaking'] = False
                            current_audio_task_ref['task'] = None
                        
                        greeting_sent = True
                
                elif event == "media":
                    payload = data.get("media", {}).get("payload")
                    if payload:
                        audio = await deepgram_service.convert_twilio_audio(payload, session_id)
                        if audio:
                            await deepgram_service.process_audio_chunk(session_id, audio)
                
                elif event == "stop":
                    break
            
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                break
    
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    finally:
        logger.info(f"🧹 Cleanup {call_sid}")
        
        # Clean up interrupted text storage
        interrupted_text_storage.pop(call_sid, None)
        
        try:
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
                    call_record.duration = call_duration
                    call_record.status = 'completed'
                    call_record.ended_at = datetime.utcnow()
                    db.commit()
            except:
                db.rollback()
        except:
            pass
        
        try:
            await deepgram_service.close_session(session_id)
        except:
            pass
        
        call_context.pop(call_sid, None)
        db.close()


@router.post("/initiate-outbound-call")
async def initiate_outbound_call(request: Request):
    """Initiate an outbound call with pre-warming"""
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
                "error": "Missing required fields"
            }
        
        logger.info(f"📞 Initiating call to {to_number}")
        
        # PRE-WARM: Fetch and cache agent data BEFORE call connects
        cache_key = f"{company_id}_{agent_id}"
        if not get_cached_agent(cache_key):
            logger.info(f"⚡ PRE-WARMING agent cache for {cache_key}...")
            try:
                master_agent = await agent_config_service.get_master_agent(company_id, agent_id)
                company_name = company_service.get_company_name_by_id(company_id)
                agent_cache[cache_key] = {
                    'agent': master_agent,
                    'company_name': company_name,
                    'cached_at': datetime.utcnow()
                }
                logger.info(f"✅ Agent data cached - greeting will be instant!")
            except Exception as e:
                logger.error(f"Pre-warm failed: {e}")
        
        # Build callback URL
        ws_domain = settings.base_url
        callback_url = (
            f"{ws_domain}/api/v1/twilio-elevenlabs/outbound-connect"
            f"?company_id={company_id}"
            f"&agent_id={agent_id}"
            f"&customer_name={quote(customer_name)}"
            f"&campaign_id={campaign_id}"
        )
        
        # Initiate call
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
        
        # Background DB save
        async def _save():
            db = None
            try:
                db = SessionLocal()
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
                if db:
                    db.rollback()
            finally:
                if db:
                    db.close()
        
        asyncio.create_task(_save())
        
        return {
            "success": True,
            "call_sid": call.sid,
            "to_number": to_number,
            "status": call.status
        }
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }
