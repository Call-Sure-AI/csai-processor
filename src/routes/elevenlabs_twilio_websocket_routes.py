"""
ElevenLabs Twilio WebSocket Integration Routes
Based on the official ElevenLabs-Twilio integration documentation
"""
import asyncio
import logging
import json
import base64
import uuid
from typing import Dict, Any, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
import httpx
from config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/elevenlabs-twilio", tags=["ElevenLabs Twilio WebSocket"])

# Global connection manager for active WebSocket connections
class TwilioElevenLabsConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.connection_metadata: Dict[str, Dict[str, Any]] = {}
        
    async def connect(self, websocket: WebSocket, connection_id: str, metadata: Dict[str, Any]):
        await websocket.accept()
        self.active_connections[connection_id] = websocket
        self.connection_metadata[connection_id] = metadata
        logger.info(f"WebSocket connection established: {connection_id}")
        
    def disconnect(self, connection_id: str):
        if connection_id in self.active_connections:
            del self.active_connections[connection_id]
        if connection_id in self.connection_metadata:
            del self.connection_metadata[connection_id]
        logger.info(f"WebSocket connection closed: {connection_id}")
        
    async def send_message(self, connection_id: str, message: Dict[str, Any]):
        if connection_id in self.active_connections:
            try:
                await self.active_connections[connection_id].send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"Error sending message to {connection_id}: {str(e)}")
                self.disconnect(connection_id)

# Global connection manager instance
connection_manager = TwilioElevenLabsConnectionManager()

async def synthesize_text_with_elevenlabs(text: str, voice_id: str = None) -> bytes:
    """Synthesize text using ElevenLabs API"""
    if not settings.eleven_labs_api_key:
        raise ValueError("ElevenLabs API key not configured")
    
    voice_id = voice_id or settings.voice_id or "21m00Tcm4TlvDq8ikWAM"  # Default voice
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={
                    "xi-api-key": settings.eleven_labs_api_key,
                    "Content-Type": "application/json"
                },
                json={
                    "text": text,
                    "model_id": "eleven_flash_v2_5",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                        "style": 0.0,
                        "use_speaker_boost": True
                    }
                },
                params={
                    "output_format": "ulaw_8000"  # Twilio-compatible format
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                return response.content
            else:
                logger.error(f"ElevenLabs API error: {response.status_code} - {response.text}")
                raise Exception(f"ElevenLabs API error: {response.status_code}")
                
    except Exception as e:
        logger.error(f"Error synthesizing text with ElevenLabs: {str(e)}")
        raise

@router.post("/call/incoming")
async def handle_incoming_call(request: Request):
    """Handle incoming Twilio call - returns TwiML with WebSocket connection"""
    try:
        # Extract form data from Twilio
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        logger.info(f"Handling incoming call {call_sid} from {from_number}")
        
        # Generate unique connection ID
        connection_id = f"call_{call_sid}_{uuid.uuid4().hex[:8]}"
        
        # Get base URL for WebSocket connection
        base_url = settings.base_url or settings.webhook_base_url or "http://localhost:8001"
        if base_url.startswith("http://"):
            base_url = base_url.replace("http://", "wss://")
        elif base_url.startswith("https://"):
            base_url = base_url.replace("https://", "wss://")
        else:
            base_url = f"wss://{base_url}"
        
        # Create WebSocket URL
        websocket_url = f"{base_url}/api/v1/elevenlabs-twilio/call/connection"
        
        # Generate TwiML response with WebSocket connection
        response = VoiceResponse()
        connect = Connect()
        stream = Stream(url=websocket_url)
        connect.append(stream)
        response.append(connect)
        
        logger.info(f"Generated TwiML for call {call_sid} with WebSocket: {websocket_url}")
        
        return Response(content=str(response), media_type="text/xml")
        
    except Exception as e:
        logger.error(f"Error handling incoming call: {str(e)}")
        # Return fallback TwiML
        response = VoiceResponse()
        response.say("Hello! This is a test call from ElevenLabs integration.", voice="alice", language="en-US")
        return Response(content=str(response), media_type="text/xml")

@router.websocket("/call/connection")
async def websocket_connection(websocket: WebSocket):
    """WebSocket endpoint for Twilio-ElevenLabs audio streaming"""
    connection_id = None
    
    try:
        await websocket.accept()
        logger.info("WebSocket connection accepted")
        
        # Generate connection ID
        connection_id = f"ws_{uuid.uuid4().hex[:8]}"
        
        # Store connection metadata
        metadata = {
            "connection_id": connection_id,
            "connected_at": asyncio.get_event_loop().time(),
            "call_sid": None,
            "stream_sid": None
        }
        
        await connection_manager.connect(websocket, connection_id, metadata)
        
        # Handle incoming messages from Twilio
        while True:
            try:
                # Receive message from Twilio
                data = await websocket.receive_text()
                message = json.loads(data)
                
                logger.debug(f"Received message: {message}")
                
                # Handle different message types
                event_type = message.get("event")
                
                if event_type == "start":
                    # Call started - store call and stream info
                    start_data = message.get("start", {})
                    call_sid = start_data.get("callSid")
                    stream_sid = start_data.get("streamSid")
                    
                    metadata["call_sid"] = call_sid
                    metadata["stream_sid"] = stream_sid
                    
                    logger.info(f"Call started - CallSid: {call_sid}, StreamSid: {stream_sid}")
                    
                    # Synthesize and send audio
                    await handle_call_start(connection_id, stream_sid, call_sid)
                    
                elif event_type == "media":
                    # Handle incoming audio from caller (optional)
                    media_data = message.get("media", {})
                    payload = media_data.get("payload")
                    
                    if payload:
                        # Decode and process incoming audio
                        audio_data = base64.b64decode(payload)
                        logger.debug(f"Received audio data: {len(audio_data)} bytes")
                        
                        # Here you could process the audio (STT, etc.)
                        # For now, we'll just log it
                        
                elif event_type == "stop":
                    # Call ended
                    logger.info(f"Call ended for connection {connection_id}")
                    break
                    
                else:
                    logger.warning(f"Unknown event type: {event_type}")
                    
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected: {connection_id}")
                break
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON received: {str(e)}")
                continue
            except Exception as e:
                logger.error(f"Error processing message: {str(e)}")
                continue
                
    except Exception as e:
        logger.error(f"Error in WebSocket connection: {str(e)}")
    finally:
        if connection_id:
            connection_manager.disconnect(connection_id)

async def handle_call_start(connection_id: str, stream_sid: str, call_sid: str):
    """Handle call start - synthesize text and send audio to Twilio"""
    try:
        # Text to synthesize (you can make this configurable)
        text = "Hello! This is a test call using ElevenLabs voice synthesis. You can now hang up. Thank you."
        
        logger.info(f"Synthesizing text for call {call_sid}: {text}")
        
        # Synthesize text using ElevenLabs
        audio_data = await synthesize_text_with_elevenlabs(text)
        
        logger.info(f"Synthesized audio: {len(audio_data)} bytes")
        
        # Send audio to Twilio via WebSocket
        await send_audio_to_twilio(connection_id, stream_sid, audio_data)
        
    except Exception as e:
        logger.error(f"Error handling call start: {str(e)}")
        # Send error message to Twilio
        await connection_manager.send_message(connection_id, {
            "event": "error",
            "error": str(e)
        })

async def send_audio_to_twilio(connection_id: str, stream_sid: str, audio_data: bytes):
    """Send audio data to Twilio via WebSocket"""
    try:
        # Convert audio to base64
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
        # Create media message for Twilio
        media_message = {
            "streamSid": stream_sid,
            "event": "media",
            "media": {
                "payload": audio_base64
            }
        }
        
        # Send to Twilio
        await connection_manager.send_message(connection_id, media_message)
        
        logger.info(f"Sent audio to Twilio for stream {stream_sid}")
        
    except Exception as e:
        logger.error(f"Error sending audio to Twilio: {str(e)}")

@router.get("/test")
async def test_endpoint():
    """Test endpoint to verify the service is working"""
    return {
        "status": "ok",
        "message": "ElevenLabs Twilio WebSocket service is running",
        "elevenlabs_configured": bool(settings.eleven_labs_api_key),
        "active_connections": len(connection_manager.active_connections)
    }

@router.get("/connections")
async def get_connections():
    """Get all active WebSocket connections"""
    connections = []
    for conn_id, metadata in connection_manager.connection_metadata.items():
        connections.append({
            "connection_id": conn_id,
            "call_sid": metadata.get("call_sid"),
            "stream_sid": metadata.get("stream_sid"),
            "connected_at": metadata.get("connected_at")
        })
    
    return {
        "active_connections": len(connections),
        "connections": connections
    }

@router.post("/test-synthesis")
async def test_synthesis(text: str = "Hello! This is a test of ElevenLabs text-to-speech synthesis."):
    """Test text-to-speech synthesis"""
    try:
        audio_data = await synthesize_text_with_elevenlabs(text)
        
        return StreamingResponse(
            iter([audio_data]),
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "attachment; filename=test_synthesis.mp3"
            }
        )
        
    except Exception as e:
        logger.error(f"Error in test synthesis: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
