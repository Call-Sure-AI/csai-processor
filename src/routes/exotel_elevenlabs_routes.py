# src/routes/exotel_elevenlabs_routes.py

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database.config import SessionLocal
from database.models import CallType, ConversationTurn, Call
from services.speech.deepgram_ws_service import DeepgramWebSocketService
from services.voice.elevenlabs_service import elevenlabs_service
from services.rag.rag_service import get_rag_service
from services.agent_config_service import agent_config_service
from services.prompt_template_service import prompt_template_service
from services.company_service import company_service
from services.call_recording_service import call_recording_service
from services.agent_tools import execute_function
from config.settings import settings
from datetime import datetime
import logging
import json
import asyncio
import base64
import audioop

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/exotel-elevenlabs", tags=["exotel-elevenlabs"])

# Shared state (same pattern as Twilio)
call_context = {}
agent_cache = {}
interrupted_text_storage = {}
AGENT_CACHE_TTL = 300

from_number_global = None
to_number_global = None


def get_cached_agent(cache_key: str):
    """Get cached agent data if not expired"""
    if cache_key in agent_cache:
        cached = agent_cache[cache_key]
        if (datetime.utcnow() - cached["cached_at"]).total_seconds() < AGENT_CACHE_TTL:
            return cached
    return None


async def prewarm_agent_cache(company_id: str, agent_id: str):
    """Pre-warm agent cache in background"""
    cache_key = f"{company_id}:{agent_id}"
    try:
        master_agent = await agent_config_service.get_master_agent(company_id, agent_id)
        company_name = company_service.get_company_name_by_id(company_id)
        agent_cache[cache_key] = {
            "agent": master_agent,
            "company_name": company_name,
            "cached_at": datetime.utcnow()
        }
        logger.info(f"✅ Pre-warmed agent cache: {cache_key}")
    except Exception as e:
        logger.error(f"Pre-warm failed: {e}")


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


def convert_exotel_audio_to_deepgram(base64_audio: str) -> bytes:
    """
    Convert Exotel's 16-bit PCM 8kHz audio to Deepgram-compatible format
    Exotel sends: 16-bit PCM, 8kHz, mono, base64-encoded
    """
    try:
        # Decode from base64
        pcm_data = base64.b64decode(base64_audio)
        
        # Exotel sends 16-bit PCM at 8kHz - Deepgram expects same
        # No conversion needed, just pass through
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
        # Decode mulaw from base64
        mulaw_data = base64.b64decode(mulaw_chunk)
        
        # Convert mulaw to 16-bit PCM (linear)
        pcm_data = audioop.ulaw2lin(mulaw_data, 2)  # 2 = 16-bit width
        
        # Encode back to base64 for Exotel
        return base64.b64encode(pcm_data).decode('utf-8')
        
    except Exception as e:
        logger.error(f"Error converting audio for Exotel: {e}")
        return ""


async def stream_elevenlabs_audio_to_exotel(
    websocket: WebSocket,
    stream_id: str,
    text: str,
    stop_flag_ref: dict,
    is_speaking_ref: dict = None
):
    """
    Stream audio to Exotel with proper state management
    Similar to Twilio but uses Exotel's message format
    """
    if not stream_id:
        logger.error("No stream_id for Exotel")
        return
    
    chunk_count = 0
    try:
        logger.info(f"🔊 Generating audio: '{text[:50]}...'")
        
        if stop_flag_ref.get("stop", False):
            logger.warning("Stop flag already set - aborting")
            return
        
        # Stream audio chunks
        async for audio_chunk in elevenlabs_service.generate(text):
            # Check for interruption
            if stop_flag_ref.get("stop", False):
                logger.warning(f"⚠️ STOP FLAG at chunk {chunk_count}")
                try:
                    # Send clear command to Exotel
                    await websocket.send_json({
                        "event": "clear",
                        "streamSid": stream_id
                    })
                    logger.info("✅ CLEAR sent to Exotel")
                except:
                    pass
                return
            
            if audio_chunk and stream_id:
                # Convert mulaw to PCM for Exotel
                exotel_audio = convert_elevenlabs_to_exotel(audio_chunk)
                
                # Send in Exotel format
                message = {
                    "event": "media",
                    "streamSid": stream_id,
                    "media": {
                        "payload": exotel_audio
                    }
                }
                await websocket.send_json(message)
                chunk_count += 1
        
        logger.info(f"✓ Sent {chunk_count} chunks to Exotel")
        
        # Wait for playback (same timing as Twilio)
        estimated_playback = chunk_count * 0.02 + 0.5
        logger.info(f"⏳ Waiting {estimated_playback:.1f}s for playback")
        
        elapsed = 0
        check_interval = 0.05
        while elapsed < estimated_playback:
            if stop_flag_ref.get("stop", False):
                logger.warning(f"⚠️ STOP during playback at {elapsed:.2f}s")
                try:
                    await websocket.send_json({
                        "event": "clear",
                        "streamSid": stream_id
                    })
                except:
                    pass
                return
            
            await asyncio.sleep(check_interval)
            elapsed += check_interval
        
        logger.info("✅ Audio playback completed")
        
    except asyncio.CancelledError:
        logger.warning(f"⚠️ Audio task CANCELLED at chunk {chunk_count}")
        try:
            await websocket.send_json({
                "event": "clear",
                "streamSid": stream_id
            })
            logger.info("✅ CLEAR sent on cancellation")
        except:
            pass
        raise
        
    except Exception as e:
        logger.error(f"Error streaming to Exotel: {e}")
        
    finally:
        if is_speaking_ref:
            is_speaking_ref["speaking"] = False
            logger.debug("Speaking flag reset")


@router.post("/incoming-call")
async def handle_incoming_call_exotel(request: Request):
    """
    Exotel incoming call handler
    Similar to Twilio but returns Exotel-compatible XML
    """
    global from_number_global, to_number_global
    
    try:
        form_data = await request.form()
        
        # Exotel uses similar parameter names to Twilio
        call_sid = form_data.get("CallSid") or form_data.get("Sid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        from_number_global = from_number
        to_number_global = to_number
        
        company_id = request.query_params.get("company_id")
        agent_id = request.query_params.get("agent_id")
        
        logger.info(f"📞 INCOMING EXOTEL CALL - CallSid: {call_sid}")
        logger.info(f"   From: {from_number} → To: {to_number}")
        logger.info(f"   Company: {company_id}, Agent: {agent_id}")
        
        # Store context
        call_context[call_sid] = {
            "company_id": company_id,
            "agent_id": agent_id,
            "from_number": from_number,
            "to_number": to_number
        }
        
        # Pre-warm cache
        cache_key = f"{company_id}:{agent_id}"
        if not get_cached_agent(cache_key):
            asyncio.create_task(prewarm_agent_cache(company_id, agent_id))
        
        # Generate WebSocket URL
        ws_domain = settings.base_url.replace("https://", "").replace("http://", "")
        stream_url = f"wss://{ws_domain}/api/v1/exotel-elevenlabs/media-stream?call_sid={call_sid}"
        
        logger.info(f"🔗 Stream URL: {stream_url}")
        
        # Generate Exotel-compatible XML (similar to TwiML but may have differences)
        # Exotel uses "Stream" applet in their flow
        response_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_url}" />
    </Connect>
</Response>"""
        
        logger.info(f"📄 Exotel XML Response generated")
        return Response(content=response_xml, media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error handling Exotel call: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        error_response = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">An error occurred. Please try again.</Say>
</Response>"""
        return Response(content=error_response, media_type="application/xml")


@router.websocket("/media-stream")
async def handle_media_stream_exotel(websocket: WebSocket):
    """
    Exotel WebSocket handler - handles bidirectional audio streaming
    Similar to Twilio but with Exotel's message format
    """
    global from_number_global, to_number_global
    
    try:
        await websocket.accept()
        logger.info("✅ Exotel WebSocket ACCEPTED")
    except Exception as e:
        logger.error(f"Failed to accept WebSocket: {e}")
        return
    
    # Initialize state (same as Twilio)
    conversation_transcript = []
    call_sid = websocket.query_params.get("call_sid")
    first_message_data = None
    stop_audio_flag = {"stop": False}
    greeting_sent = False
    greeting_start_time = None
    is_agent_speaking_ref = {"speaking": False}
    current_audio_task_ref = {"task": None}
    
    logger.info(f"Query params: {dict(websocket.query_params)}")
    
    # Wait for Exotel 'start' event if no call_sid
    if not call_sid:
        logger.info("No call_sid in query, waiting for Exotel 'start' event...")
        try:
            for attempt in range(3):
                message = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                data = json.loads(message)
                event_type = data.get("event")
                
                logger.info(f"Received event #{attempt + 1}: {event_type}")
                
                if event_type == "connected":
                    logger.info("Connected event")
                    continue
                elif event_type == "start":
                    call_sid = data.get("start", {}).get("callSid") or data.get("start", {}).get("Sid")
                    first_message_data = data
                    logger.info(f"✅ Extracted call_sid: {call_sid}")
                    break
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for start event")
            await websocket.close(code=1008)
            return
    
    if not call_sid:
        logger.error("Could not obtain call_sid")
        await websocket.close(code=1008)
        return
    
    logger.info(f"📞 Call SID validated: {call_sid}")
    
    # Get context
    context = call_context.get(call_sid, {})
    company_id = context.get("company_id")
    master_agent_id = context.get("agent_id")
    
    logger.info(f"📋 Company: {company_id}, Agent: {master_agent_id}")
    
    # Get agent configuration (with cache)
    cache_key = f"{company_id}:{master_agent_id}"
    cached = get_cached_agent(cache_key)
    
    if cached:
        logger.info("✅ Using CACHED agent data")
        master_agent = cached["agent"]
    else:
        logger.info("⏳ Fetching agent configuration...")
        master_agent = await agent_config_service.get_master_agent(company_id, master_agent_id)
        company_name = company_service.get_company_name_by_id(company_id)
        agent_cache[cache_key] = {
            "agent": master_agent,
            "company_name": company_name,
            "cached_at": datetime.utcnow()
        }
    
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
        "provider": "exotel"
    }
    
    # Generate greeting
    greeting = prompt_template_service.generate_greeting(master_agent, company_id, agent_name)
    logger.info(f"💬 Greeting: '{greeting[:50]}...'")
    
    # Initialize services
    db = SessionLocal()
    deepgram_service = DeepgramWebSocketService()
    rag = get_rag_service()
    stream_id = None
    
    try:
        # Define callbacks (same as Twilio)
        async def on_interim_transcript(session_id: str, transcript: str, confidence: float):
            """Handle interim transcripts for interruption"""
            nonlocal stream_id, current_audio_task_ref
            
            word_count = len(transcript.split())
            if word_count < 2:
                return
            
            is_speaking = is_agent_speaking_ref["speaking"]
            logger.info(f"🎯 INTERRUPT CHECK: '{transcript[:30]}' | speaking={is_speaking}")
            
            # Send clear to Exotel
            if stream_id:
                try:
                    await websocket.send_json({
                        "event": "clear",
                        "streamSid": stream_id
                    })
                    logger.info(f"✅ CLEAR sent to Exotel for {stream_id}")
                except Exception as e:
                    logger.error(f"Failed to send CLEAR: {e}")
            
            # Save interrupted text
            interrupted_text_storage[call_sid] = {
                "text": transcript,
                "timestamp": datetime.utcnow(),
                "confidence": confidence
            }
            logger.info(f"💾 Saved interrupted text: '{transcript}'")
            
            if not is_speaking:
                return
            
            logger.warning(f"⚠️ INTERRUPTING AGENT! {transcript}")
            stop_audio_flag["stop"] = True
            is_agent_speaking_ref["speaking"] = False
            
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
        
        async def on_deepgram_transcript(session_id: str, transcript: str):
            """Handle final transcripts"""
            nonlocal conversation_transcript, greeting_start_time
            
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
                interrupted_text = interrupted["text"]
                age = (datetime.utcnow() - interrupted["timestamp"]).total_seconds()
                
                if age < 10.0:
                    if interrupted_text.lower() not in transcript.lower():
                        transcript = f"{interrupted_text} {transcript}"
                        logger.info(f"📝 Combined: '{transcript}'")
            
            logger.info(f"👤 CUSTOMER: '{transcript}'")
            conversation_transcript.append({
                "role": "user",
                "content": transcript,
                "timestamp": datetime.utcnow().isoformat()
            })
            
            # Save to DB
            save_to_db_background(call_sid, "user", transcript)
            
            # Detect sentiment
            sentiment_analysis = prompt_template_service.detect_sentiment_and_urgency(
                transcript, current_agent_context
            )
            logger.info(f"📊 Sentiment: {sentiment_analysis['sentiment']}, Urgency: {sentiment_analysis['urgency']}")
            
            # Process and respond (import your existing function)
            is_agent_speaking_ref["speaking"] = True
            stop_audio_flag["stop"] = False
            
            try:
                # Build conversation messages
                conversation_messages = []
                for msg in conversation_transcript[-6:]:
                    if msg["role"] in ["user", "assistant"]:
                        conversation_messages.append({
                            "role": msg["role"],
                            "content": msg["content"]
                        })
                
                # Add system prompt
                system_prompt = prompt_template_service.build_system_prompt_with_guardrails(
                    master_agent, call_type="incoming"
                )
                conversation_messages.insert(0, {"role": "system", "content": system_prompt})
                
                # Call LLM
                logger.info("🤖 Calling LLM...")
                response = await rag.llm_with_functions.ainvoke(conversation_messages)
                
                # Handle function calls or direct response
                if hasattr(response, 'additional_kwargs') and 'function_call' in response.additional_kwargs:
                    function_call = response.additional_kwargs['function_call']
                    function_name = function_call['name']
                    arguments = json.loads(function_call['arguments'])
                    
                    logger.info(f"🔧 Function: {function_name}")
                    
                    llm_response = await execute_function(
                        function_name=function_name,
                        arguments=arguments,
                        company_id=company_id,
                        call_sid=call_sid
                    )
                    logger.info(f"✅ Result: {llm_response[:80]}...")
                else:
                    llm_response = response.content
                    logger.info(f"💬 Direct response: {llm_response[:80]}...")
                
                if stop_audio_flag.get("stop", False):
                    logger.info("Skipping audio - interrupted")
                    return
                
                # Save to transcript
                conversation_transcript.append({
                    "role": "assistant",
                    "content": llm_response,
                    "timestamp": datetime.utcnow().isoformat()
                })
                logger.info(f"🤖 AGENT: '{llm_response[:100]}...'")
                
                # Save to DB
                save_to_db_background(call_sid, "assistant", llm_response)
                
                # Stream audio
                is_agent_speaking_ref["speaking"] = True
                audio_task = asyncio.create_task(
                    stream_elevenlabs_audio_to_exotel(
                        websocket, stream_id, llm_response,
                        stop_audio_flag, is_agent_speaking_ref
                    )
                )
                current_audio_task_ref["task"] = audio_task
                
                try:
                    await audio_task
                    logger.info("✅ Audio completed")
                except asyncio.CancelledError:
                    logger.info("Audio cancelled by interruption")
                    raise
                finally:
                    is_agent_speaking_ref["speaking"] = False
                    current_audio_task_ref["task"] = None
                    
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"❌ Error: {e}")
                import traceback
                logger.error(traceback.format_exc())
        
        # Initialize Deepgram
        session_id = f"deepgram_{call_sid}"
        logger.info("🎙️ Starting Deepgram...")
        deepgram_init_task = asyncio.create_task(
            deepgram_service.initialize_session(
                session_id=session_id,
                callback=on_deepgram_transcript,
                interruption_callback=on_interim_transcript
            )
        )
        
        # Send greeting immediately if we have stream_id
        if first_message_data and first_message_data.get("event") == "start":
            stream_id = first_message_data.get("streamSid") or first_message_data.get("start", {}).get("streamSid")
            logger.info(f"📡 Stream started: {stream_id}")
            logger.info("🔊 Sending greeting NOW")
            
            is_agent_speaking_ref["speaking"] = True
            stop_audio_flag["stop"] = False
            greeting_start_time = datetime.utcnow()
            
            current_audio_task = asyncio.create_task(
                stream_elevenlabs_audio_to_exotel(
                    websocket, stream_id, greeting,
                    stop_audio_flag, is_agent_speaking_ref
                )
            )
            current_audio_task_ref["task"] = current_audio_task
            
            try:
                await current_audio_task
                logger.info("✅ Greeting completed")
            except asyncio.CancelledError:
                logger.info("Greeting cancelled")
            finally:
                is_agent_speaking_ref["speaking"] = False
                current_audio_task_ref["task"] = None
            
            greeting_sent = True
        
        # Wait for Deepgram
        deepgram_connected = await deepgram_init_task
        if not deepgram_connected:
            logger.error("Deepgram failed")
            await websocket.close()
            return
        
        logger.info("✅ Deepgram ready - entering message loop")
        
        # Main message loop - handle Exotel WebSocket messages
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                data = json.loads(message)
                event = data.get("event")
                
                if event == "connected":
                    logger.debug("Connected event")
                    
                elif event == "start":
                    if not greeting_sent:
                        stream_id = data.get("streamSid")
                        logger.info(f"📡 Stream started late: {stream_id}")
                        
                        is_agent_speaking_ref["speaking"] = True
                        stop_audio_flag["stop"] = False
                        greeting_start_time = datetime.utcnow()
                        
                        current_audio_task = asyncio.create_task(
                            stream_elevenlabs_audio_to_exotel(
                                websocket, stream_id, greeting,
                                stop_audio_flag, is_agent_speaking_ref
                            )
                        )
                        current_audio_task_ref["task"] = current_audio_task
                        
                        try:
                            await current_audio_task
                        except:
                            pass
                        finally:
                            is_agent_speaking_ref["speaking"] = False
                            current_audio_task_ref["task"] = None
                        
                        greeting_sent = True
                
                elif event == "media":
                    # Exotel sends audio in 'media.payload' as base64-encoded PCM
                    payload = data.get("media", {}).get("payload")
                    if payload:
                        # Convert Exotel PCM to Deepgram format
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
                logger.error(f"Error in message loop: {e}")
                break
    
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
    finally:
        logger.info(f"Cleaning up {call_sid}")
        call_context.pop(call_sid, None)
        interrupted_text_storage.pop(call_sid, None)
        
        try:
            await deepgram_service.close_session(session_id)
        except:
            pass
        
        db.close()


@router.post("/call-status")
async def handle_call_status_exotel(request: Request):
    """Handle Exotel call status callbacks"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid") or form_data.get("Sid")
        call_status = form_data.get("CallStatus") or form_data.get("Status")
        
        logger.info(f"Exotel status: {call_sid} → {call_status}")
        
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
                    logger.info(f"Updated DB: {call_sid}")
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
        logger.error(f"Error: {e}")
        return {"status": "error", "message": str(e)}