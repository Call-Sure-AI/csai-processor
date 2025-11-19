from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database.config import get_db
from database.models import ConversationTurn
from services.speech.deepgram_ws_service import DeepgramWebSocketService
from services.voice.elevenlabs_service import elevenlabs_service
from services.rag.rag_service import get_rag_service
from twilio.twiml.voice_response import VoiceResponse, Connect
from datetime import datetime
from config.settings import settings
import logging
import json
import asyncio
import uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/twilio-elevenlabs", tags=["twilio-elevenlabs"])

@router.post("/incoming-call")
async def handle_incoming_call_elevenlabs(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Incoming call handler with ElevenLabs voice
    Use this URL in Twilio: https://processor.callsure.ai/api/v1/twilio-elevenlabs/incoming-call
    """
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        
        company_id = request.query_params.get("company_id")
        agent_id = request.query_params.get("agent_id")
        
        logger.info(f"📞 ElevenLabs call: {call_sid}, company={company_id}")
        
        # Generate TwiML to connect to Media Stream
        response = VoiceResponse()
        
        # Connect to WebSocket for bidirectional audio streaming
        connect = Connect()
        
        # Build WebSocket URL
        ws_domain = settings.base_url.replace('https://', '').replace('http://', '')
        stream_url = f"wss://{ws_domain}/api/v1/twilio-elevenlabs/media-stream?call_sid={call_sid}&company_id={company_id}&agent_id={agent_id}"
        
        connect.stream(url=stream_url)
        response.append(connect)
        
        logger.info(f"✅ Media stream initiated: {call_sid}")
        return Response(content=str(response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error handling call: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        response = VoiceResponse()
        response.say("An error occurred. Please try again.", voice="Polly.Joanna")
        return Response(content=str(response), media_type="application/xml")

@router.websocket("/media-stream")
async def handle_media_stream(
    websocket: WebSocket,
    call_sid: str,
    company_id: str,
    agent_id: str
):
    """Handle bidirectional audio streaming with Deepgram + ElevenLabs"""
    await websocket.accept()
    logger.info(f"🔌 WebSocket connected: {call_sid}")
    
    from database.config import SessionLocal
    db = SessionLocal()
    
    rag = get_rag_service()
    deepgram_service = DeepgramWebSocketService()
    stream_sid = None
    is_agent_speaking = False
    greeting_sent = False
    
    try:
        # Callback when Deepgram detects complete speech
        async def on_deepgram_transcript(session_id: str, transcript: str):
            nonlocal is_agent_speaking
            
            if is_agent_speaking or not transcript.strip():
                return
            
            logger.info(f"📝 User: '{transcript}'")
            
            # Save user input
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
            
            # Get RAG response
            is_agent_speaking = True
            
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
                logger.info(f"🤖 Agent: '{llm_response[:100]}...'")
                
                # Save agent response
                db.add(ConversationTurn(
                    call_sid=call_sid,
                    role="assistant",
                    content=llm_response,
                    created_at=datetime.utcnow()
                ))
                db.commit()
                
                # Stream audio via ElevenLabs
                await stream_elevenlabs_audio(websocket, stream_sid, llm_response)
                
            except Exception as e:
                logger.error(f"RAG error: {e}")
                import traceback
                logger.error(traceback.format_exc())
                await stream_elevenlabs_audio(
                    websocket, 
                    stream_sid, 
                    "I'm having trouble right now. Please try again."
                )
            
            is_agent_speaking = False
        
        # Initialize Deepgram session
        session_id = f"deepgram_{call_sid}"
        deepgram_connected = await deepgram_service.initialize_session(
            session_id=session_id,
            callback=on_deepgram_transcript
        )
        
        if not deepgram_connected:
            logger.error(f"Failed to connect to Deepgram for {call_sid}")
            await websocket.close()
            return
        
        logger.info(f"✅ Deepgram connected for {call_sid}")
        
        # Handle incoming WebSocket messages from Twilio
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                data = json.loads(message)
                
                event = data.get("event")
                
                if event == "start":
                    stream_sid = data.get("streamSid")
                    logger.info(f"🎙️ Stream started: {stream_sid}")
                    
                    # Send greeting after stream starts
                    if not greeting_sent:
                        await asyncio.sleep(0.8)  # Wait for stream to stabilize
                        await stream_elevenlabs_audio(
                            websocket,
                            stream_sid,
                            "Thank you for calling Callsure. How may I help you?"
                        )
                        greeting_sent = True
                    
                elif event == "media":
                    # Audio from customer -> send to Deepgram
                    if not is_agent_speaking:
                        payload = data.get("media", {}).get("payload")
                        if payload:
                            # Convert Twilio audio format to Deepgram format
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
                    logger.info(f"🛑 Stream stopped: {stream_sid}")
                    break
                    
            except asyncio.TimeoutError:
                # No message received, continue
                continue
            except WebSocketDisconnect:
                logger.info(f"🔌 WebSocket disconnected: {call_sid}")
                break
            except Exception as e:
                logger.error(f"Error processing message: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                
    except Exception as e:
        logger.error(f"Error in media stream: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        # Cleanup
        try:
            await deepgram_service.close_session(session_id)
        except:
            pass
        db.close()
        logger.info(f"✅ Cleaned up: {call_sid}")

async def stream_elevenlabs_audio(websocket: WebSocket, stream_sid: str, text: str):
    """Stream ElevenLabs audio to Twilio"""
    try:
        logger.info(f"🎤 Streaming audio: '{text[:50]}...'")
        
        chunk_count = 0
        async for audio_chunk in elevenlabs_service.generate(text):
            if audio_chunk and stream_sid:
                # Send to Twilio Media Stream
                message = {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {
                        "payload": audio_chunk
                    }
                }
                await websocket.send_json(message)
                chunk_count += 1
        
        logger.info(f"✓ Sent {chunk_count} audio chunks")
        
    except Exception as e:
        logger.error(f"Error streaming audio: {str(e)}")
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
