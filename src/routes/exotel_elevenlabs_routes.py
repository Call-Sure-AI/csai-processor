# src/routes/exotel_elevenlabs_routes.py - COMPLETE EXOTEL IMPLEMENTATION
#
# Feature parity with twilio_elevenlabs_routes.py:
# ✅ Interrupted text is SAVED and PROCESSED
# ✅ is_speaking stays TRUE during playback
# ✅ Audio STOPS when interrupted (proper task cancellation)
# ✅ Fast-path for simple responses
# ✅ Background DB writes (non-blocking)
# ✅ Booking orchestration integration
# ✅ Intent routing service
# ✅ RAG routing service
# ✅ Sentiment analysis
# ✅ Agent caching with TTL
# ✅ Pre-warming for outbound calls
# ✅ S3 upload on cleanup

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database.config import SessionLocal
from database.models import CallType, ConversationTurn, Call
from services.speech.deepgram_ws_service import DeepgramWebSocketService
from services.voice.elevenlabs_service import elevenlabs_service
from services.rag.rag_service import get_rag_service
from services.rag_routing_service import rag_routing_service
from services.datetime_parser_service import datetime_parser_service
from services.booking_orchestration_service import booking_orchestrator, BookingState
from services.company_service import company_service
from services.intent_router_service import intent_router_service
from services.agent_config_service import agent_config_service
from services.prompt_template_service import prompt_template_service
from services.call_recording_service import call_recording_service
from services.agent_tools import execute_function
from services.intent_detection_service import intent_detection_service
from services.slot_manager_service import SlotManagerService
from config.settings import settings
from datetime import datetime, timedelta
from urllib.parse import quote
import logging
import json
import asyncio
import base64
import audioop
import uuid
import httpx

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/exotel-elevenlabs", tags=["exotel-elevenlabs"])

# Shared state
call_context = {}
agent_cache = {}
interrupted_text_storage = {}
AGENT_CACHE_TTL = 300  # 5 minutes

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


def convert_exotel_audio_to_deepgram(base64_audio: str) -> bytes:
    """
    Convert Exotel's 16-bit PCM 8kHz audio to Deepgram format
    Exotel sends: 16-bit PCM, 8kHz, mono, base64-encoded
    """
    try:
        pcm_data = base64.b64decode(base64_audio)
        # Exotel sends 16-bit PCM at 8kHz - Deepgram expects same
        return pcm_data
    except Exception as e:
        logger.error(f"Error converting Exotel audio: {e}")
        return b""


def convert_elevenlabs_to_exotel(mulaw_chunk: str) -> str:
    """
    Convert ElevenLabs mulaw audio to Exotel's format
    ElevenLabs: mulaw 8kHz base64
    Exotel expects: 16-bit PCM 8kHz base64
    """
    try:
        mulaw_data = base64.b64decode(mulaw_chunk)
        pcm_data = audioop.ulaw2lin(mulaw_data, 2)  # 2 = 16-bit width
        return base64.b64encode(pcm_data).decode('utf-8')
    except Exception as e:
        logger.error(f"Error converting audio for Exotel: {e}")
        return ""


async def stream_elevenlabs_audio_optimized(
    websocket: WebSocket, 
    stream_sid: str, 
    text: str, 
    stop_flag_ref: dict,
    is_speaking_ref: dict = None
):
    """
    Stream audio with proper speaking state management.
    Adapted for Exotel (uses PCM instead of mulaw)
    """
    if not stream_sid:
        logger.error("No stream_sid")
        return
    
    chunk_count = 0
    
    try:
        logger.info(f"🔊 Generating audio: '{text[:50]}...'")
        
        if stop_flag_ref.get('stop', False):
            logger.warning("🛑 Stop flag already set - aborting audio")
            return
        
        # Stream audio chunks with interruption checking
        async for audio_chunk in elevenlabs_service.generate(text):
            if stop_flag_ref.get('stop', False):
                logger.warning(f"🛑 STOP FLAG DETECTED at chunk {chunk_count} - halting!")
                try:
                    await websocket.send_json({"event": "clear", "streamSid": stream_sid})
                    logger.info("✅ CLEAR sent during chunk streaming")
                except:
                    pass
                return
            
            if audio_chunk and stream_sid:
                # Convert mulaw to PCM for Exotel
                exotel_audio = convert_elevenlabs_to_exotel(audio_chunk)
                
                message = {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": exotel_audio}
                }
                await websocket.send_json(message)
                chunk_count += 1
        
        logger.info(f"✓ Sent {chunk_count} chunks to Exotel")
        
        # Keep is_speaking=True for estimated playback duration
        estimated_playback_seconds = (chunk_count * 0.02) + 0.5
        logger.info(f"⏳ Waiting {estimated_playback_seconds:.1f}s for Exotel playback")
        
        # Wait for Exotel to finish playing, but check for interruptions
        elapsed = 0
        check_interval = 0.05
        
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


@router.post("/incoming-call")
async def handle_incoming_call_exotel(request: Request):
    """Exotel incoming call handler"""
    global from_number_global, to_number_global
    
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid") or form_data.get("Sid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        from_number_global = from_number
        to_number_global = to_number

        company_id = request.query_params.get("company_id")
        agent_id = request.query_params.get("agent_id")
        
        logger.info(f"📞 INCOMING EXOTEL CALL - CallSid: {call_sid}")
        logger.info(f"   From: {from_number}, To: {to_number}")
        logger.info(f"   Company: {company_id}, Agent: {agent_id}")
        
        # PRE-WARM: Start fetching agent data immediately
        cache_key = f"{company_id}_{agent_id}"
        if not get_cached_agent(cache_key):
            asyncio.create_task(_prewarm_agent_cache(company_id, agent_id))
        
        # Store context
        call_context[call_sid] = {
            "company_id": company_id,
            "agent_id": agent_id,
            "from_number": from_number,
            "to_number": to_number
        }
        
        # Generate Exotel-compatible XML
        ws_domain = settings.base_url.replace('https://', '').replace('http://', '')
        stream_url = f"wss://{ws_domain}/api/v1/exotel-elevenlabs/media-stream?call_sid={call_sid}"
        
        logger.info(f"Stream URL: {stream_url}")
        
        response_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_url}" />
    </Connect>
</Response>"""
        
        logger.info(f"📄 Exotel XML Response generated")
        return Response(content=response_xml, media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        error_response = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">An error occurred. Please try again.</Say>
</Response>"""
        return Response(content=error_response, media_type="application/xml")


@router.post("/call-status")
async def handle_call_status_exotel(request: Request):
    """Handle Exotel call status callbacks"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid") or form_data.get("Sid")
        call_status = form_data.get("CallStatus") or form_data.get("Status")
        
        logger.info(f"📞 Exotel status update: {call_sid} -> {call_status}")
        
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


@router.websocket("/media-stream")
async def handle_media_stream_exotel(websocket: WebSocket):
    """Exotel WebSocket handler with ALL features from Twilio implementation"""
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
        logger.info("No call_sid in query, waiting for Exotel 'start' event...")
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
                    call_sid = data.get("start", {}).get("callSid") or data.get("start", {}).get("Sid")
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
        'company_id': company_id,
        'provider': 'exotel'
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
        # FIXED INTERRUPTION CALLBACK
        async def on_interim_transcript(session_id: str, transcript: str, confidence: float):
            """INSTANT interruption - saves text and clears Exotel buffer"""
            nonlocal stream_sid
            nonlocal current_audio_task
            
            word_count = len(transcript.split())
            if word_count < 2:
                return
            
            is_speaking = is_agent_speaking_ref['speaking']
            logger.info(f"🎯 INTERRUPT CHECK: '{transcript[:30]}' | is_speaking={is_speaking}")
            
            # ALWAYS send CLEAR to Exotel
            if stream_sid:
                try:
                    await websocket.send_json({"event": "clear", "streamSid": stream_sid})
                    logger.info(f"✅ CLEAR sent to Exotel for {stream_sid}")
                except Exception as e:
                    logger.error(f"Failed to send CLEAR: {e}")
            
            # FIX: ALWAYS save the interrupted text
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
        
        # FIXED TRANSCRIPT CALLBACK
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
                
                if age < 10.0:
                    if interrupted_text.lower() not in transcript.lower():
                        transcript = f"{interrupted_text} {transcript}"
                        logger.info(f"📝 Combined with interrupted text: '{transcript}'")
                    elif len(transcript.split()) < 3 and len(interrupted_text.split()) >= 3:
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
            stream_sid = first_message_data.get("streamSid") or first_message_data.get("start", {}).get("streamSid")
            logger.info(f"✅ Stream started: {stream_sid}")
            
            # SEND GREETING IMMEDIATELY
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
                        stream_sid = data.get("streamSid") or data.get("start", {}).get("streamSid")
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
                    # Process audio for transcription (Exotel sends PCM)
                    payload = data.get("media", {}).get("payload")
                    if payload:
                        audio = convert_exotel_audio_to_deepgram(payload)
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
                    call_record.provider = 'exotel'
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
                        provider='exotel',
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


# PROCESS AND RESPOND - Same logic as Twilio
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
        
        # Stream response
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
    is_speaking_ref: dict = None,
    audio_task_ref: dict = None
):
    """Process outbound call with sales/booking logic"""
    
    rag = get_rag_service()
    
    try:
        if stop_audio_flag.get('stop', False):
            logger.info("Skipping LLM - interrupted")
            return
        
        # Get booking session if exists
        booking_session = booking_orchestrator.get_session(call_sid)
        is_booking_mode = booking_session is not None
        
        # Check for rejection
        rejection_detected = intent_analysis.get('rejection_detected', False)
        buying_intent = intent_analysis.get('buying_intent', False)
        
        if rejection_detected:
            logger.warning(f"❌ REJECTION DETECTED: {transcript}")
            
            # Polite exit
            goodbye_message = "I understand. Thank you for your time. Have a great day!"
            
            conversation_transcript.append({
                'role': 'assistant',
                'content': goodbye_message,
                'timestamp': datetime.utcnow().isoformat(),
                'action': 'polite_exit'
            })
            
            save_to_db_background(call_sid, "assistant", goodbye_message)
            
            if is_speaking_ref:
                is_speaking_ref['speaking'] = True
            
            audio_task = asyncio.create_task(
                stream_elevenlabs_audio_optimized(
                    websocket, stream_sid, goodbye_message,
                    stop_audio_flag, is_speaking_ref
                )
            )
            
            if audio_task_ref:
                audio_task_ref['task'] = audio_task
            
            try:
                await audio_task
            except asyncio.CancelledError:
                pass
            finally:
                if is_speaking_ref:
                    is_speaking_ref['speaking'] = False
                if audio_task_ref:
                    audio_task_ref['task'] = None
            
            # End call gracefully
            await asyncio.sleep(1.0)
            try:
                await websocket.close()
            except:
                pass
            
            return
        
        # FIX #6: Only fetch datetime/slots when in booking mode
        datetime_info = None
        available_slots = []
        available_slots_text = ""
        
        if is_booking_mode and booking_session:
            # Parse datetime if customer mentions time
            datetime_info = datetime_parser_service.parse_datetime_from_text(transcript)
            
            # Only fetch slots if we don't have date/time yet
            collected = booking_session.get('collected_data', {})
            if not collected.get('date') or not collected.get('time'):
                try:
                    slot_manager = SlotManagerService()
                    available_slots = await slot_manager.get_available_slots(
                        company_id=company_id,
                        start_date=datetime.utcnow().date(),
                        days_ahead=7
                    )
                    
                    if available_slots:
                        slots_formatted = []
                        for slot in available_slots[:3]:
                            slot_dt = slot['datetime']
                            slots_formatted.append(
                                f"{slot_dt.strftime('%A, %B %d')} at {slot_dt.strftime('%I:%M %p')}"
                            )
                        available_slots_text = " or ".join(slots_formatted)
                        logger.info(f"📅 Available slots: {available_slots_text}")
                except Exception as e:
                    logger.error(f"Error fetching slots: {e}")
        
        # Update booking session if datetime found
        if isinstance(datetime_info, dict) and datetime_info.get('parsed_successfully') and booking_session:
            logger.info(f"📅 AI extracted: {datetime_info.get('user_friendly')}")
            
            if datetime_info.get('date'):
                booking_orchestrator.update_session_data(call_sid, 'date', datetime_info['date'])
            if datetime_info.get('time'):
                booking_orchestrator.update_session_data(call_sid, 'time', datetime_info['time'])
            if datetime_info.get('datetime_iso'):
                booking_orchestrator.update_session_data(call_sid, 'datetime_iso', datetime_info['datetime_iso'])
        
        # Extract email if present
        import re
        email_pattern = r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}'
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
        if is_booking_mode:
            # BOOKING MODE PROMPT
            customer = booking_session.get('customer_info', {})
            collected = booking_session.get('collected_data', {})
            customer_name = customer.get('name', 'there')
            customer_phone = customer.get('phone', 'unknown')
            
            next_action = booking_orchestrator.get_next_action(call_sid) if booking_session else ""
            prompt_hint = "Ask for datetime" if not next_action else next_action
            
            has_date = collected.get('date') is not None
            has_time = collected.get('time') is not None
            has_email = customer.get('email') is not None
            
            now = datetime.utcnow()
            
            prompt = f"""Sales call for {company_name} with {customer_name}.

Booking Progress:
- Name: {customer_name}
- Phone: {customer_phone}
- Date: {collected.get('date') if has_date else 'NEED'}
- Time: {collected.get('time') if has_time else 'NEED'}
- Email: {customer.get('email') if has_email else 'NEED'}

Today: {now.strftime('%A, %B %d, %Y')}
Available Time Slots: {available_slots_text if available_slots_text else 'Suggest tomorrow 10 AM, 2 PM, or 4 PM'}

Next step: {next_action or prompt_hint}

CRITICAL RULES:
1. We already have name and phone - NEVER ask for them
2. Primary goal: Collect date, time, and email to complete booking
3. Flow: date → time → verify slot → email → confirm booking
4. PRODUCT QUESTIONS: If customer asks about the product/features/benefits:
   - Give a brief answer (1-2 sentences max)
   - Then immediately guide back to booking
5. Keep ALL responses to 1-2 sentences maximum
6. NO support tickets in sales mode
7. If user talks in english, reply back in english, else reply in user's language, except for tool calls etc.
8. If user speaks in hindi, make sure to reply in hinglish only, except for tool calls etc.

Be conversational but focused on booking."""
        
        else:
            # SALES MODE PROMPT
            customer_name = conversation_transcript[0].get('customer_name', 'there') if conversation_transcript else 'there'
            buying_readiness = intent_analysis.get('buying_readiness', 50)
            
            prompt = f"""Sales call for {company_name}.

Customer: {customer_name}
Interest Level: {buying_readiness}%

Approach based on interest:
- 70%+: "Would you like to book a demo?"
- 40-70%: Share 1 key benefit + ask booking interest
- <40%: Ask 1 qualifying question

Rules:
- Max 1-2 sentences
- NO support tickets in sales mode
- Focus on moving toward booking

Be conversational."""
        
        conversation_messages.insert(0, {
            'role': 'system',
            'content': prompt
        })
        
        # Check for interruption before LLM call
        if stop_audio_flag.get('stop', False):
            logger.info("Skipping LLM - interrupted")
            return
        
        # Single LLM call with functions
        logger.info("🤖 Calling LLM...")
        response = await rag.llm_with_functions.ainvoke(conversation_messages)
        
        # Handle function calls
        if hasattr(response, 'additional_kwargs') and 'function_call' in response.additional_kwargs:
            function_call = response.additional_kwargs['function_call']
            function_name = function_call['name']
            arguments = json.loads(function_call['arguments'])
            
            logger.info(f"🔧 Function: {function_name}")
            
            # Block ticket creation during sales
            if function_name == 'create_ticket' and call_type == 'outgoing':
                logger.warning(f"🚫 BLOCKED create_ticket during sales")
                llm_response = "Let me help you book that demo. What date works best?"
            else:
                # Update state before execution
                if function_name == 'check_slot_availability' and booking_session:
                    booking_orchestrator.transition_state(
                        call_sid, 
                        BookingState.CHECKING_AVAILABILITY, 
                        "Verifying slot"
                    )
                
                llm_response = await execute_function(
                    function_name=function_name,
                    arguments=arguments,
                    company_id=company_id,
                    call_sid=call_sid,
                    campaign_id=None,
                    user_timezone='UTC',
                    business_hours={'start': '09:00', 'end': '18:00'}
                )
                
                logger.info(f"✅ Result: {llm_response[:80]}...")
                
                # Update state after execution
                if function_name == 'check_slot_availability':
                    if 'available' in llm_response.lower() and 'available not' not in llm_response.lower():
                        booking_orchestrator.update_session_data(call_sid, 'slot_available', True)
                        booking_orchestrator.transition_state(
                            call_sid,
                            BookingState.COLLECTING_EMAIL,
                            "Slot confirmed"
                        )
                    else:
                        booking_orchestrator.transition_state(
                            call_sid,
                            BookingState.COLLECTING_DATE,
                            "Slot unavailable"
                        )
                
                elif function_name == 'verify_customer_email':
                    booking_orchestrator.update_session_data(call_sid, 'email_verified', True)
                    booking_orchestrator.transition_state(
                        call_sid,
                        BookingState.CONFIRMING_BOOKING,
                        "Email verified"
                    )
                
                elif function_name == 'create_booking':
                    if 'booking id' in llm_response.lower() or 'scheduled' in llm_response.lower() and 'failed' not in llm_response.lower():
                        booking_orchestrator.transition_state(
                            call_sid,
                            BookingState.COMPLETED,
                            "Booking complete!"
                        )
                        logger.info(f"🎉 BOOKING COMPLETED for {customer_name}")
                    else:
                        logger.error(f"❌ BOOKING FAILED: {llm_response[:100]}")
                        booking_orchestrator.transition_state(
                            call_sid,
                            BookingState.COLLECTING_DATE,
                            "Booking failed"
                        )
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
        
        logger.info(f"🤖 AGENT: '{llm_response[:100]}...'")
        
        # Background DB save
        save_to_db_background(call_sid, "assistant", llm_response)
        
        # Stream audio with state management
        if is_speaking_ref:
            is_speaking_ref['speaking'] = True
        
        logger.info(f"🔊 Setting is_speaking=TRUE")
        
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
            logger.info(f"✅ Audio completed - setting is_speaking=FALSE")
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
        logger.error(f"❌ Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        error_msg = "Could you repeat that?"
        
        if is_speaking_ref:
            is_speaking_ref['speaking'] = True
        
        error_task = asyncio.create_task(
            stream_elevenlabs_audio_optimized(
                websocket, stream_sid, error_msg,
                stop_audio_flag, is_speaking_ref
            )
        )
        
        if audio_task_ref:
            audio_task_ref['task'] = error_task
        
        try:
            await error_task
        except:
            pass
        finally:
            if is_speaking_ref:
                is_speaking_ref['speaking'] = False
            if audio_task_ref:
                audio_task_ref['task'] = None


@router.post("/outbound-connect")
async def handle_outbound_connect_exotel(request: Request):
    """Exotel outbound call handler"""
    global from_number_global, to_number_global
    
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid") or form_data.get("Sid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        from_number_global = from_number
        to_number_global = to_number
        
        campaign_id = request.query_params.get("campaign_id")
        company_id = request.query_params.get("company_id")
        agent_id = request.query_params.get("agent_id")
        customer_name = request.query_params.get("customer_name", "")
        
        logger.info(f"📞 OUTBOUND EXOTEL CALL - CallSid: {call_sid}")
        logger.info(f"   From: {from_number}, To: {to_number}")
        logger.info(f"   Company: {company_id}, Agent: {agent_id}, Customer: {customer_name}")
        
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
        
        # Generate Exotel XML response
        ws_domain = settings.base_url.replace('https://', '').replace('http://', '')
        stream_url = f"wss://{ws_domain}/api/v1/exotel-elevenlabs/outbound-stream?call_sid={call_sid}"
        
        response_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_url}" />
    </Connect>
</Response>"""
        
        return Response(content=response_xml, media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        error_response = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">An error occurred.</Say>
</Response>"""
        return Response(content=error_response, media_type="application/xml")


@router.websocket("/outbound-stream")
async def handle_outbound_stream_exotel(websocket: WebSocket):
    """Exotel outbound call streaming with ALL fixes applied"""
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
    
    is_agent_speaking_ref = {'speaking': False}
    current_audio_task_ref = {'task': None}
    
    # Extract call_sid from first message if not in query
    if not call_sid:
        try:
            for attempt in range(3):
                message = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                data = json.loads(message)
                event_type = data.get("event")
                
                if event_type == "connected":
                    continue
                elif event_type == "start":
                    call_sid = data.get("start", {}).get("callSid") or data.get("start", {}).get("Sid")
                    first_message_data = data
                    break
        except asyncio.TimeoutError:
            await websocket.close(code=1008)
            return
    
    if not call_sid:
        await websocket.close(code=1008)
        return
    
    logger.info(f"📞 Outbound Call SID: {call_sid}")
    
    # Get context
    context = call_context.get(call_sid, {})
    company_id = context.get("company_id")
    agent_id = context.get("agent_id")
    customer_name = context.get("customer_name", "")
    campaign_id = context.get("campaign_id")
    call_type = context.get("call_type", "outgoing")
    
    logger.info(f"📋 Company: {company_id}, Agent: {agent_id}, Customer: {customer_name}")
    
    # PRE-WARM agent cache
    cache_key = f"{company_id}_{agent_id}"
    if not get_cached_agent(cache_key):
        logger.info(f"🔥 PRE-WARMING agent cache for {cache_key}...")
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
    
    # Get cached agent
    cached = get_cached_agent(cache_key)
    if cached:
        master_agent = cached['agent']
    else:
        start_time = datetime.utcnow()
        master_agent = await agent_config_service.get_master_agent(company_id, agent_id)
        company_name = company_service.get_company_name_by_id(company_id)
        
        agent_cache[cache_key] = {
            'agent': master_agent,
            'company_name': company_name,
            'cached_at': datetime.utcnow()
        }
        init_duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(f"⏱️ Fetched in {init_duration:.2f}s")
    
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
        'call_type': call_type,
        'provider': 'exotel'
    }
    
    # Generate personalized greeting
    additional_context = master_agent.get('additional_context', {})
    business_context = additional_context.get('businessContext', '')
    
    greeting = f"Hello {customer_name}! This is {agent_name} calling from {company_service.get_company_name_by_id(company_id)}. "
    greeting += f"I'm reaching out because we offer {business_context}. "
    greeting += "Would you be interested in learning more?"
    
    logger.info(f"💬 Greeting pre-generated: '{greeting[:50]}...'")
    
    # Initialize services
    db = SessionLocal()
    deepgram_service = DeepgramWebSocketService()
    rag = get_rag_service()
    stream_sid = None
    
    try:
        # Same interruption and transcript callbacks as incoming
        async def on_interim_transcript(session_id: str, transcript: str, confidence: float):
            nonlocal stream_sid, current_audio_task
            
            word_count = len(transcript.split())
            if word_count < 2:
                return
            
            is_speaking = is_agent_speaking_ref['speaking']
            logger.info(f"🎯 INTERRUPT CHECK: '{transcript[:30]}' | speaking={is_speaking}")
            
            if stream_sid:
                try:
                    await websocket.send_json({"event": "clear", "streamSid": stream_sid})
                    logger.info(f"✅ CLEAR sent to Exotel for {stream_sid}")
                except Exception as e:
                    logger.error(f"Failed to send CLEAR: {e}")
            
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
            
            if current_audio_task_ref['task'] and not current_audio_task_ref['task'].done():
                current_audio_task_ref['task'].cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(current_audio_task_ref['task']),
                        timeout=0.3
                    )
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                current_audio_task_ref['task'] = None
        
        async def on_deepgram_transcript(session_id: str, transcript: str):
            nonlocal conversation_transcript, greeting_start_time, rejection_count
            
            if not transcript.strip():
                return
            
            # Echo protection
            if greeting_start_time:
                elapsed = (datetime.utcnow() - greeting_start_time).total_seconds()
                if elapsed < 1.5:
                    logger.debug(f"Ignoring early echo: {transcript}")
                    return
            
            # Handle interrupted text
            if call_sid in interrupted_text_storage:
                interrupted = interrupted_text_storage.pop(call_sid)
                interrupted_text = interrupted['text']
                age = (datetime.utcnow() - interrupted['timestamp']).total_seconds()
                
                if age < 10.0:
                    if interrupted_text.lower() not in transcript.lower():
                        transcript = f"{interrupted_text} {transcript}"
                        logger.info(f"📝 Combined: '{transcript}'")
            
            logger.info(f"👤 CUSTOMER: '{transcript}'")
            
            # Fast-path for simple responses
            simple_words = ['yes', 'no', 'hello', 'hi', 'okay', 'sure', 'yeah', 'nope', 'yep', 'hey']
            is_simple = len(transcript.split()) <= 5 and any(w in transcript.lower() for w in simple_words)
            
            if is_simple and call_type == 'outgoing':
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
                # Full intent detection
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
            
            # Process and respond
            is_agent_speaking_ref['speaking'] = True
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
                    current_agent_id=agent_id,
                    company_id=company_id,
                    conversation_transcript=conversation_transcript,
                    intent_analysis=intent_analysis,
                    call_type=call_type,
                    is_speaking_ref=is_agent_speaking_ref,
                    audio_task_ref=current_audio_task_ref
                )
            except asyncio.CancelledError:
                logger.info("Response cancelled")
            except Exception as e:
                logger.error(f"Response error: {e}")
            finally:
                is_agent_speaking_ref['speaking'] = False
                current_audio_task_ref['task'] = None
        
        # Start Deepgram
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
            stream_sid = first_message_data.get("streamSid") or first_message_data.get("start", {}).get("streamSid")
            logger.info(f"✅ Stream started: {stream_sid}")
            
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
        
        # Wait for Deepgram
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
                        except:
                            pass
                        finally:
                            is_agent_speaking_ref['speaking'] = False
                            current_audio_task_ref['task'] = None
                        
                        greeting_sent = True
                
                elif event == "media":
                    payload = data.get("media", {}).get("payload")
                    if payload:
                        audio = convert_exotel_audio_to_deepgram(payload)
                        if audio:
                            await deepgram_service.process_audio_chunk(session_id, audio)
                
                elif event == "stop":
                    logger.info("STREAM STOPPED")
                    break
            
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                logger.info("WebSocket disconnected")
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                break
    
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    finally:
        logger.info(f"🧹 Cleaning up {call_sid}")
        
        interrupted_text_storage.pop(call_sid, None)
        
        try:
            call_duration = int((datetime.utcnow() - call_metadata['start_time']).total_seconds())
            
            s3_urls = await call_recording_service.save_call_data(
                call_sid=call_sid,
                company_id=company_id,
                agent_id=agent_id,
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
                    call_record.provider = 'exotel'
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
async def initiate_outbound_call_exotel(request: Request):
    """Initiate an outbound call via Exotel API"""
    try:
        data = await request.json()
        
        to_number = data.get("to_number")
        customer_name = data.get("customer_name", "")
        company_id = data.get("company_id")
        agent_id = data.get("agent_id")
        campaign_id = data.get("campaign_id", "")
        from_number = data.get("from_number", settings.exotel_phone_number)
        
        if not to_number or not company_id or not agent_id:
            return {
                "success": False,
                "error": "Missing required fields: to_number, company_id, agent_id"
            }
        
        logger.info(f"📞 Initiating Exotel outbound call")
        logger.info(f"   To: {to_number}, Customer: {customer_name}")
        
        # Build callback URL
        ws_domain = settings.base_url
        callback_url = f"{ws_domain}/api/v1/exotel-elevenlabs/outbound-connect"
        callback_url += f"?company_id={company_id}"
        callback_url += f"&agent_id={agent_id}"
        callback_url += f"&customer_name={quote(customer_name)}"
        callback_url += f"&campaign_id={campaign_id}"
        
        # Exotel API call (similar to Twilio but using Exotel SDK/API)
        # Note: You'll need to implement Exotel API integration
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.exotel.com/v1/Accounts/{settings.exotel_account_sid}/Calls/connect",
                auth=(settings.exotel_api_key, settings.exotel_api_token),
                data={
                    "From": from_number,
                    "To": to_number,
                    "Url": callback_url,
                    "StatusCallback": f"{ws_domain}/api/v1/exotel-elevenlabs/call-status",
                    "TimeLimit": 1800  # 30 minutes
                }
            )
            
            if response.status_code in [200, 201]:
                call_data = response.json()
                call_sid = call_data.get("Sid") or call_data.get("call_sid")
                
                logger.info(f"✅ Call initiated: {call_sid}")
                
                # Background DB save
                async def _save():
                    db = None
                    try:
                        db = SessionLocal()
                        new_call = Call(
                            call_sid=call_sid,
                            company_id=company_id,
                            from_number=from_number,
                            to_number=to_number,
                            call_type=CallType.outgoing,
                            status='initiated',
                            provider='exotel',
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
                    "call_sid": call_sid,
                    "to_number": to_number,
                    "status": "initiated"
                }
            else:
                logger.error(f"Exotel API error: {response.status_code}")
                return {
                    "success": False,
                    "error": f"Exotel API error: {response.text}"
                }
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return {"success": False, "error": str(e)}
