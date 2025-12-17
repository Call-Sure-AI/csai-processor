from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import Response
from services.telephony import get_telephony_provider
from config.settings import settings
import logging

# Import shared resources from your working Twilio routes
from routes.twilio_elevenlabs_routes import (
    call_context,
    agent_cache,
    interrupted_text_storage,
    get_cached_agent,
    prewarm_agent_cache,
    save_to_db_background,
    stream_elevenlabs_audio_optimized,
    process_and_respond_incoming,
    process_and_respond_outbound
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/calls", tags=["unified-calls"])

# Global variables (shared with twilio_elevenlabs_routes)
from_number_global = None
to_number_global = None


@router.post("/incoming")
async def handle_incoming_call_unified(
    request: Request,
    provider: str = Query(default="twilio")
):
    """
    Unified incoming call handler - works for Twilio AND Exotel
    Provider is determined by 'provider' query parameter
    """
    global from_number_global, to_number_global
    
    try:
        form_data = await request.form()
        
        # Extract call details (works for both Twilio and Exotel)
        call_sid = form_data.get("CallSid") or form_data.get("Sid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        from_number_global = from_number
        to_number_global = to_number
        
        company_id = request.query_params.get("company_id")
        agent_id = request.query_params.get("agent_id")
        
        logger.info(f"📞 INCOMING CALL [{provider.upper()}]")
        logger.info(f"   CallSid: {call_sid}")
        logger.info(f"   From: {from_number} → To: {to_number}")
        logger.info(f"   Company: {company_id}, Agent: {agent_id}")
        
        # Store context for WebSocket
        call_context[call_sid] = {
            "company_id": company_id,
            "agent_id": agent_id,
            "from_number": from_number,
            "to_number": to_number,
            "provider": provider
        }
        
        # Pre-warm agent cache (optional optimization)
        cache_key = f"{company_id}:{agent_id}"
        if not get_cached_agent(cache_key):
            asyncio.create_task(prewarm_agent_cache(company_id, agent_id))
        
        # Get provider instance
        telephony = get_telephony_provider(provider)
        
        # Generate WebSocket URL with provider parameter
        ws_domain = settings.base_url.replace("https://", "").replace("http://", "")
        websocket_url = f"wss://{ws_domain}/api/v1/calls/media-stream?call_sid={call_sid}&provider={provider}"
        
        logger.info(f"🔗 WebSocket URL: {websocket_url}")
        
        # Generate provider-specific TwiML/XML response
        response_xml = telephony.generate_connection_response(
            websocket_url=websocket_url,
            call_context=call_context[call_sid]
        )
        
        logger.info(f"✅ TwiML/XML Response generated for {provider}")
        return Response(content=response_xml, media_type="application/xml")
        
    except Exception as e:
        logger.error(f"❌ Error handling incoming call: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        # Generic error response (works for both providers)
        error_response = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">An error occurred. Please try again.</Say>
</Response>"""
        return Response(content=error_response, media_type="application/xml")


@router.websocket("/media-stream")
async def handle_media_stream_unified(
    websocket: WebSocket,
    provider: str = Query(default="twilio")
):
    """
    Unified WebSocket handler - works for Twilio AND Exotel
    Uses provider-specific audio conversion but shared processing logic
    """
    global from_number_global, to_number_global
    
    try:
        await websocket.accept()
        logger.info(f"✅ WebSocket ACCEPTED [{provider.upper()}]")
    except Exception as e:
        logger.error(f"Failed to accept WebSocket: {str(e)}")
        return
    
    # Import required services
    from services.speech.deepgram_ws_service import DeepgramWebSocketService
    from services.agent_config_service import agent_config_service
    from services.company_service import company_service
    from services.prompt_template_service import prompt_template_service
    from services.rag.rag_service import get_rag_service
    from database.config import SessionLocal
    from datetime import datetime
    import json
    import asyncio
    
    # Initialize state
    conversation_transcript = []
    call_sid = websocket.query_params.get("call_sid")
    first_message_data = None
    stop_audio_flag = {"stop": False}
    greeting_sent = False
    greeting_start_time = None
    is_agent_speaking_ref = {"speaking": False}
    current_audio_task_ref = {"task": None}
    
    # Get provider instance
    telephony = get_telephony_provider(provider)
    logger.info(f"📡 Using provider: {telephony.provider_name}")
    
    # Extract call_sid from first message if not in query params
    if not call_sid:
        logger.info("No call_sid in query, waiting for start event...")
        try:
            for attempt in range(3):
                message = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
                data = json.loads(message)
                event_type = data.get("event")
                
                if event_type == "connected":
                    logger.debug("Connected event")
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
    
    # Get call context
    context = call_context.get(call_sid, {})
    company_id = context.get("company_id")
    master_agent_id = context.get("agent_id")
    stored_provider = context.get("provider", provider)
    
    logger.info(f"📋 Context: Company={company_id}, Agent={master_agent_id}, Provider={stored_provider}")
    
    # Get agent configuration (reuse your existing logic)
    cache_key = f"{company_id}:{master_agent_id}"
    cached = get_cached_agent(cache_key)
    
    if cached:
        logger.info("✅ Using CACHED agent data")
        master_agent = cached["agent"]
    else:
        logger.info("⏳ Fetching agent configuration...")
        master_agent = await agent_config_service.get_master_agent(company_id, master_agent_id)
        company_name = company_service.get_company_name_by_id(company_id)
        
        # Cache it
        from datetime import datetime
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
        "provider": stored_provider
    }
    
    # Generate greeting
    greeting = prompt_template_service.generate_greeting(master_agent, company_id, agent_name)
    logger.info(f"💬 Greeting: {greeting[:50]}...")
    
    # Initialize services
    db = SessionLocal()
    deepgram_service = DeepgramWebSocketService()
    rag = get_rag_service()
    stream_id = None
    call_state = {"first_interaction": True, "interaction_count": 0}
    
    try:
        # Define callbacks (reuse your existing logic)
        async def on_interim_transcript(session_id: str, transcript: str, confidence: float):
            """Handle interim transcripts for interruption"""
            nonlocal stream_id, current_audio_task_ref
            
            word_count = len(transcript.split())
            if word_count < 2:
                return
            
            is_speaking = is_agent_speaking_ref["speaking"]
            logger.info(f"🎧 INTERRUPT CHECK: {transcript[:30]} | speaking={is_speaking}")
            
            if is_speaking:
                logger.warning(f"⚠️ INTERRUPTING AGENT! {transcript}")
                stop_audio_flag["stop"] = True
                is_agent_speaking_ref["speaking"] = False
                
                # Send clear command via provider
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
            """Handle final transcripts - reuses your existing process_and_respond_incoming"""
            nonlocal conversation_transcript, greeting_start_time
            
            if not transcript.strip():
                return
            
            # Echo protection
            if greeting_start_time:
                elapsed = (datetime.utcnow() - greeting_start_time).total_seconds()
                if elapsed < 1.5:
                    logger.debug(f"Ignoring early echo: {transcript}")
                    return
            
            # Handle interrupted text (your existing logic)
            if call_sid in interrupted_text_storage:
                interrupted = interrupted_text_storage.pop(call_sid)
                interrupted_text = interrupted["text"]
                age = (datetime.utcnow() - interrupted["timestamp"]).total_seconds()
                
                if age < 10.0:
                    if interrupted_text.lower() not in transcript.lower():
                        transcript = f"{interrupted_text} {transcript}"
                        logger.info(f"💬 Combined: {transcript}")
            
            logger.info(f"👤 CUSTOMER: {transcript}")
            conversation_transcript.append({
                "role": "user",
                "content": transcript,
                "timestamp": datetime.utcnow().isoformat()
            })
            
            # Save to DB (background)
            save_to_db_background(call_sid, "user", transcript)
            
            # Process and respond using your existing function
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
                    sentiment_analysis=None,
                    urgent_acknowledgment=None,
                    call_metadata=call_metadata,
                    is_speaking_ref=is_agent_speaking_ref,
                    audio_task_ref=current_audio_task_ref
                )
            except asyncio.CancelledError:
                logger.info("Response cancelled")
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
            stream_id = first_message_data.get("streamSid") or first_message_data.get("start", {}).get("streamSid")
            logger.info(f"📡 Stream ID: {stream_id}")
            
            is_agent_speaking_ref["speaking"] = True
            stop_audio_flag["stop"] = False
            greeting_start_time = datetime.utcnow()
            
            # Stream greeting via provider
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
        
        # Wait for Deepgram
        deepgram_connected = await deepgram_init_task
        if not deepgram_connected:
            logger.error("Deepgram connection failed")
            await websocket.close()
            return
        
        logger.info("✅ Deepgram ready - entering message loop")
        
        # Main message loop - provider handles media streaming
        await telephony.handle_media_stream(websocket, deepgram_service, stream_id, session_id)
        
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        logger.info(f"🧹 Cleaning up {call_sid}")
        call_context.pop(call_sid, None)
        interrupted_text_storage.pop(call_sid, None)
        
        try:
            await deepgram_service.close_session(session_id)
        except:
            pass
        
        db.close()


@router.post("/outbound")
async def initiate_outbound_call_unified(
    request: Request,
    provider: str = Query(default="twilio")
):
    """
    Unified outbound call - works for Twilio AND Exotel
    """
    try:
        data = await request.json()
        
        logger.info(f"📞 OUTBOUND CALL [{provider.upper()}]")
        logger.info(f"   From: {data.get('from_number')} → To: {data.get('to_number')}")
        
        # Get provider instance
        telephony = get_telephony_provider(provider)
        
        # Initiate call via provider
        result = await telephony.initiate_call(
            from_number=data["from_number"],
            to_number=data["to_number"],
            webhook_url=data["webhook_url"],
            status_callback_url=data.get("status_callback_url"),
            record=data.get("record", False),
            timeout=data.get("timeout", 60),
            **data.get("extra_params", {})  # Provider-specific params
        )
        
        logger.info(f"✅ Call initiated via {provider}: {result.get('call_sid')}")
        return result
        
    except KeyError as e:
        logger.error(f"Missing field: {e}")
        return {"error": f"Missing required field: {str(e)}"}, 400
    except Exception as e:
        logger.error(f"Outbound call failed: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e)}, 500


@router.post("/status")
async def handle_call_status_unified(request: Request):
    """
    Unified status callback - works for both Twilio and Exotel
    """
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid") or form_data.get("Sid")
        call_status = form_data.get("CallStatus") or form_data.get("Status")
        
        logger.info(f"📊 Status update: {call_sid} → {call_status}")
        
        # Update database (background task)
        async def update():
            from database.models import Call
            db = None
            try:
                db = SessionLocal()
                call_record = db.query(Call).filter_by(call_sid=call_sid).first()
                
                if call_record:
                    call_record.status = call_status
                    if call_status in ["completed", "failed", "busy", "no-answer"]:
                        call_record.ended_at = datetime.utcnow()
                    db.commit()
                    logger.info(f"✅ Updated DB: {call_sid}")
                    
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
        logger.error(f"Error handling status: {e}")
        return {"status": "error", "message": str(e)}