from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database.config import get_db
from database.models import ConversationTurn
from services.speech.deepgram_ws_service import DeepgramWebSocketService
from services.voice.elevenlabs_service import elevenlabs_service
from services.rag.rag_service import get_rag_service
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
from datetime import datetime
from config.settings import settings
import logging
import json
import asyncio


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/twilio-elevenlabs", tags=["twilio-elevenlabs"])

@router.post("/incoming-call")
async def handle_incoming_call_elevenlabs(
    request: Request,
    db: Session = Depends(get_db)
):
    """Incoming call handler with ElevenLabs via Media Streams"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        company_id = request.query_params.get("company_id")
        agent_id = request.query_params.get("agent_id")
        
        logger.info(f"📞 INCOMING CALL - CallSid: {call_sid}")
        logger.info(f"   From: {from_number}, To: {to_number}")
        logger.info(f"   Company: {company_id}, Agent: {agent_id}")
        
        # Generate TwiML response
        response = VoiceResponse()
        
        # Start with a pause to let connection stabilize
        response.pause(length=1)
        
        # Connect to Media Stream
        connect = Connect()
        
        # Build WebSocket URL
        ws_domain = settings.base_url.replace('https://', '').replace('http://', '')
        stream_url = f"wss://{ws_domain}/api/v1/twilio-elevenlabs/media-stream?call_sid={call_sid}&company_id={company_id}&agent_id={agent_id}"
        
        logger.info(f"📡 Stream URL: {stream_url}")
        
        # Add the Stream noun
        stream = Stream(url=stream_url)
        connect.append(stream)
        response.append(connect)
        
        twiml_str = str(response)
        logger.info(f"📋 TwiML Response:\n{twiml_str}")
        
        return Response(content=twiml_str, media_type="application/xml")
        
    except Exception as e:
        logger.error(f"❌ Error in incoming call: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        response = VoiceResponse()
        response.say("An error occurred. Please try again later.", voice="Polly.Joanna")
        return Response(content=str(response), media_type="application/xml")

@router.websocket("/media-stream")
async def handle_media_stream(
    websocket: WebSocket,
    call_sid: str = Query(None),
    company_id: str = Query(None),
    agent_id: str = Query(None)
):
    """Handle bidirectional audio streaming"""
    
    logger.info(f"🔌 WebSocket connection attempt - CallSid: {call_sid}, Company: {company_id}, Agent: {agent_id}")
    
    # ✅ Validate parameters
    if not call_sid or not company_id or not agent_id:
        logger.error(f"❌ Missing parameters: call_sid={call_sid}, company_id={company_id}, agent_id={agent_id}")
        await websocket.close(code=1008, reason="Missing required parameters")
        return
    
    try:
        # Accept the connection
        await websocket.accept()
        logger.info(f"✅ WebSocket ACCEPTED - CallSid: {call_sid}")
        
    except Exception as e:
        logger.error(f"❌ Failed to accept WebSocket: {str(e)}")
        return
        
    from database.config import SessionLocal
    db = SessionLocal()
    
    deepgram_service = DeepgramWebSocketService()
    rag = get_rag_service()
    
    stream_sid = None
    is_agent_speaking = False
    greeting_sent = False
    call_started = False
    
    try:
        # Deepgram transcript callback
        async def on_deepgram_transcript(session_id: str, transcript: str):
            nonlocal is_agent_speaking
            
            if is_agent_speaking or not transcript.strip():
                logger.debug(f"Skipping transcript (speaking={is_agent_speaking}, text='{transcript}')")
                return
            
            logger.info(f"📝 USER SAID: '{transcript}'")
            
            # Save to DB
            try:
                db.add(ConversationTurn(
                    call_sid=call_sid,
                    role="user",
                    content=transcript,
                    created_at=datetime.utcnow()
                ))
                db.commit()
                logger.info(f"💾 Saved user message to DB")
            except Exception as e:
                logger.error(f"DB save error: {e}")
            
            # Get RAG response
            is_agent_speaking = True
            logger.info(f"🤖 Getting RAG response...")
            
            try:
                response_chunks = []
                async for chunk in rag.get_answer(
                    company_id=company_id,
                    question=transcript,
                    agent_id=agent_id,
                    call_sid=call_sid
                ):
                    response_chunks.append(chunk)
                
                llm_response = "".join(response_chunks)
                logger.info(f"✅ AGENT RESPONSE: '{llm_response[:100]}...'")
                
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
                logger.error(f"❌ RAG error: {e}")
                import traceback
                logger.error(traceback.format_exc())
                await stream_elevenlabs_audio(
                    websocket, 
                    stream_sid, 
                    "I'm having trouble processing that. Could you please repeat?"
                )
            finally:
                is_agent_speaking = False
        
        # Initialize Deepgram
        session_id = f"deepgram_{call_sid}"
        logger.info(f"🎙️ Initializing Deepgram session: {session_id}")
        
        deepgram_connected = await deepgram_service.initialize_session(
            session_id=session_id,
            callback=on_deepgram_transcript
        )
        
        if not deepgram_connected:
            logger.error(f"❌ Deepgram connection failed for {call_sid}")
            await websocket.close()
            return
        
        logger.info(f"✅ Deepgram connected for {call_sid}")
        
        # Main WebSocket message loop
        logger.info(f"📨 Starting message loop for {call_sid}")
        
        while True:
            try:
                # Wait for message with timeout
                message = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                
                try:
                    data = json.loads(message)
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON: {message[:100]}")
                    continue
                
                event = data.get("event")
                
                if event == "connected":
                    logger.info(f"🔗 Twilio connected event")
                    
                elif event == "start":
                    stream_sid = data.get("streamSid")
                    call_started = True
                    logger.info(f"▶️ STREAM STARTED - StreamSid: {stream_sid}")
                    logger.info(f"   Start data: {json.dumps(data, indent=2)}")
                    
                    # Send greeting after a short delay
                    if not greeting_sent:
                        logger.info(f"🎤 Preparing to send greeting...")
                        await asyncio.sleep(1.0)  # Wait for stream to be fully ready
                        
                        greeting = "Thank you for calling Callsure. How may I help you?"
                        logger.info(f"🎤 Sending greeting: '{greeting}'")
                        
                        await stream_elevenlabs_audio(websocket, stream_sid, greeting)
                        greeting_sent = True
                        logger.info(f"✅ Greeting sent")
                    
                elif event == "media":
                    # Audio from customer
                    if not is_agent_speaking and call_started:
                        payload = data.get("media", {}).get("payload")
                        if payload:
                            # Convert and send to Deepgram
                            converted_audio = await deepgram_service.convert_twilio_audio(
                                payload, 
                                session_id
                            )
                            if converted_audio:
                                await deepgram_service.process_audio_chunk(
                                    session_id,
                                    converted_audio
                                )
                    
                elif event == "stop":
                    logger.info(f"⏹️ STREAM STOPPED - StreamSid: {stream_sid}")
                    break
                    
                elif event == "mark":
                    mark_name = data.get("mark", {}).get("name")
                    logger.info(f"📍 Mark event: {mark_name}")
                    
                else:
                    logger.debug(f"❓ Unknown event: {event}")
                    
            except asyncio.TimeoutError:
                # No message, continue
                continue
                
            except WebSocketDisconnect:
                logger.info(f"🔌 WebSocket disconnected normally - {call_sid}")
                break
                
            except Exception as e:
                logger.error(f"❌ Error in message loop: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                break
        
    except Exception as e:
        logger.error(f"❌ Fatal error in media stream: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
    finally:
        # Cleanup
        logger.info(f"🧹 Cleaning up session {call_sid}")
        try:
            await deepgram_service.close_session(session_id)
        except:
            pass
        db.close()
        logger.info(f"✅ Cleanup complete for {call_sid}")

async def stream_elevenlabs_audio(websocket: WebSocket, stream_sid: str, text: str):
    """Stream ElevenLabs audio to Twilio"""
    
    if not stream_sid:
        logger.error("❌ No stream_sid, cannot send audio")
        return
    
    try:
        logger.info(f"🎤 Generating audio for: '{text[:50]}...'")
        
        chunk_count = 0
        
        async for audio_chunk in elevenlabs_service.generate(text):
            if audio_chunk and stream_sid:
                # Send to Twilio
                message = {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {
                        "payload": audio_chunk
                    }
                }
                
                await websocket.send_json(message)
                chunk_count += 1
        
        # Send mark to know when audio finishes
        mark_message = {
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {
                "name": f"audio_complete_{chunk_count}"
            }
        }
        await websocket.send_json(mark_message)
        
        logger.info(f"✅ Sent {chunk_count} audio chunks")
        
    except Exception as e:
        logger.error(f"❌ Error streaming audio: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())


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
