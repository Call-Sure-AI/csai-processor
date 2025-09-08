"""
WebRTC Routes for ElevenLabs Twilio Integration
Handles real-time audio streaming between Twilio calls and ElevenLabs
"""
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.websockets import WebSocketState
import logging
import asyncio
import json
import base64
import queue
from datetime import datetime
from contextlib import asynccontextmanager

from services.voice.twilio_elevenlabs_integration import twilio_elevenlabs_integration
from services.speech.stt_service import SpeechToTextService
from services.llm_service import MultiProviderLLMService
from services.speech.conversation_manager_service import ConversationManager
from config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webrtc", tags=["WebRTC ElevenLabs Integration"])

# Global background task reference for cleanup
background_task: Optional[asyncio.Task] = None

# WebSocket connection manager
class WebRTCConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.connection_metadata: Dict[str, Dict[str, Any]] = {}
        
    async def connect(self, websocket: WebSocket, connection_id: str, metadata: Dict[str, Any]):
        # WebSocket should already be accepted by the route handler
        self.active_connections[connection_id] = websocket
        self.connection_metadata[connection_id] = metadata
        logger.info(f"WebRTC connection established: {connection_id}")
        
    def disconnect(self, connection_id: str):
        if connection_id in self.active_connections:
            del self.active_connections[connection_id]
        if connection_id in self.connection_metadata:
            del self.connection_metadata[connection_id]
        logger.info(f"WebRTC connection closed: {connection_id}")
        
    async def send_audio(self, connection_id: str, audio_data: bytes):
        if connection_id in self.active_connections:
            try:
                # Send audio data as base64 encoded string
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                await self.active_connections[connection_id].send_text(
                    json.dumps({
                        "event": "media",
                        "media": {
                            "payload": audio_base64
                        },
                        "timestamp": datetime.utcnow().isoformat()
                    })
                )
            except Exception as e:
                logger.error(f"Error sending audio to {connection_id}: {str(e)}")
                self.disconnect(connection_id)
                
    async def send_message(self, connection_id: str, message: Dict[str, Any]):
        if connection_id in self.active_connections:
            try:
                await self.active_connections[connection_id].send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"Error sending message to {connection_id}: {str(e)}")
                self.disconnect(connection_id)

    async def cleanup_all_connections(self):
        """Clean up all active connections during shutdown"""
        for connection_id in list(self.active_connections.keys()):
            try:
                websocket = self.active_connections[connection_id]
                if websocket.client_state not in (WebSocketState.DISCONNECTED,):
                    await websocket.close(code=1001, reason="Server shutdown")
            except Exception as e:
                logger.error(f"Error closing connection {connection_id}: {str(e)}")
            finally:
                self.disconnect(connection_id)

# Global connection manager
connection_manager = WebRTCConnectionManager()

# Global services
stt_service = SpeechToTextService()
llm_service = MultiProviderLLMService()
conversation_managers: Dict[str, ConversationManager] = {}

# Background task to send audio from ElevenLabs to WebRTC connections
async def audio_stream_worker():
    """Background worker to handle audio streaming from ElevenLabs to WebRTC connections"""
    logger.info("Audio stream worker started")
    
    try:
        while True:
            try:
                # Get all active integrations
                integrations = await twilio_elevenlabs_integration.get_all_integrations()
                
                for integration in integrations:
                    call_sid = integration['call_sid']
                    
                    # Get audio stream for this call
                    audio_queue = await twilio_elevenlabs_integration.get_call_audio_stream(call_sid)
                    if not audio_queue:
                        continue
                    
                    # Check if there's audio data
                    if not audio_queue.empty():
                        try:
                            # Get audio data (non-blocking)
                            if hasattr(audio_queue, 'get_nowait'):
                                audio_data = audio_queue.get_nowait()
                            else:
                                audio_data = await audio_queue.get()
                            
                            # Validate audio_data is bytes
                            if not isinstance(audio_data, bytes):
                                logger.warning(f"Expected bytes for audio data, got {type(audio_data)}")
                                continue
                            
                            # Find corresponding WebRTC connection
                            if call_sid in connection_manager.active_connections:
                                await connection_manager.send_audio(call_sid, audio_data)
                                    
                            # Mark task as done if it's a regular queue
                            if hasattr(audio_queue, 'task_done'):
                                audio_queue.task_done()
                                
                        except queue.Empty:
                            pass
                        except asyncio.QueueEmpty:
                            pass
                        except Exception as e:
                            logger.error(f"Error processing audio for call {call_sid}: {str(e)}")
                
                # Sleep briefly before next iteration
                await asyncio.sleep(0.1)
                
            except asyncio.CancelledError:
                logger.info("Audio stream worker cancelled")
                break
            except Exception as e:
                logger.error(f"Error in audio stream worker: {str(e)}")
                await asyncio.sleep(1.0)
                
    finally:
        logger.info("Audio stream worker stopped")

# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def webrtc_lifespan(app):
    """Lifespan context manager for startup and shutdown events"""
    global background_task
    
    # Startup
    logger.info("Starting WebRTC service...")
    background_task = asyncio.create_task(audio_stream_worker())
    logger.info("WebRTC service started")
    
    yield
    
    # Shutdown
    logger.info("Shutting down WebRTC service...")
    if background_task and not background_task.done():
        background_task.cancel()
        try:
            await background_task
        except asyncio.CancelledError:
            pass
    
    await connection_manager.cleanup_all_connections()
    logger.info("WebRTC service shutdown complete")

@router.get("/test", summary="Test WebRTC ElevenLabs Endpoint")
async def test_webrtc_elevenlabs():
    """Test endpoint to verify WebRTC ElevenLabs routes are accessible"""
    import uuid
    test_peer_id = f"twilio_test_{uuid.uuid4().hex[:8]}"
    
    return {
        "status": "success",
        "message": "WebRTC ElevenLabs endpoint is accessible",
        "test_peer_id": test_peer_id,
        "endpoints": {
            "simple_stream": f"/api/v1/webrtc/twilio-elevenlabs-stream/{test_peer_id}",
            "full_stream": f"/api/v1/webrtc/twilio-elevenlabs-stream/{test_peer_id}/default/default",
            "direct_stream": f"/api/v1/webrtc/twilio-direct-stream/{test_peer_id}"
        },
        "active_connections": len(connection_manager.active_connections),
        "connection_manager_status": "active",
        "background_task_status": "running" if background_task and not background_task.done() else "stopped"
    }

@router.get("/debug/{peer_id}", summary="Debug WebSocket Connection")
async def debug_websocket_connection(peer_id: str):
    """Debug WebSocket connection for a specific peer ID"""
    return {
        "peer_id": peer_id,
        "active_connections": len(connection_manager.active_connections),
        "connection_exists": peer_id in connection_manager.active_connections,
        "connection_manager_status": "active",
        "background_task_status": "running" if background_task and not background_task.done() else "stopped",
        "message": "Use this endpoint to debug WebSocket connections"
    }

@router.websocket("/twilio-direct-stream/{call_sid}")
async def twilio_stream_direct(
    websocket: WebSocket,
    call_sid: str
):
    """Direct Twilio WebSocket endpoint following the documentation pattern"""
    logger.info(f"=== Direct Twilio WebSocket connection for call: {call_sid} ===")
    
    try:
        await websocket.accept()
        logger.info(f"WebSocket connection accepted for call {call_sid}")
        
        # Store connection metadata
        metadata = {
            "call_sid": call_sid,
            "connected_at": datetime.utcnow(),
            "connection_type": "twilio_direct"
        }
        
        # Connect to connection manager (WebSocket already accepted)
        await connection_manager.connect(websocket, call_sid, metadata)
        
        # Send immediate confirmation to Twilio
        confirmation = {
            "event": "connected",
            "protocol": "Call",
            "version": "1.0.0"
        }
        await websocket.send_text(json.dumps(confirmation))
        logger.info(f"Sent WebSocket confirmation for call {call_sid}")
        
        # Handle incoming messages from Twilio
        while True:
            try:
                # Receive message from Twilio
                data = await websocket.receive_text()
                logger.debug(f"Received WebSocket data: {data}")
                
                message = json.loads(data)
                event_type = message.get("event")
                
                if event_type == "start":
                    # Call started - store stream info
                    start_data = message.get("start", {})
                    stream_sid = start_data.get("streamSid")
                    
                    metadata["stream_sid"] = stream_sid
                    
                    logger.info(f"Stream started - CallSid: {call_sid}, StreamSid: {stream_sid}")
                    
                    # Synthesize and send audio using ElevenLabs
                    asyncio.create_task(handle_twilio_call_start(call_sid, stream_sid))
                    
                elif event_type == "media":
                    # Handle incoming audio from caller
                    media_data = message.get("media", {})
                    payload = media_data.get("payload")
                    
                    if payload:
                        # Decode and process incoming audio
                        try:
                            audio_data = base64.b64decode(payload)
                            logger.debug(f"Received audio data: {len(audio_data)} bytes")
                            
                            # Process audio with STT for conversation
                            async def transcription_callback(session_id: str, transcribed_text: str):
                                """Callback when transcription is complete"""
                                if transcribed_text and transcribed_text.strip():
                                    logger.info(f"Transcribed speech for call {call_sid}: '{transcribed_text}'")
                                    await handle_user_speech(call_sid, transcribed_text)
                            
                            # Send audio to STT service
                            await stt_service.process_audio_chunk(
                                session_id=call_sid,
                                audio_data=audio_data,
                                callback=transcription_callback
                            )
                            
                        except Exception as decode_error:
                            logger.error(f"Error decoding audio: {str(decode_error)}")
                        
                elif event_type == "stop":
                    # Call ended
                    logger.info(f"Stream stopped for call {call_sid}")
                    break
                    
                else:
                    logger.debug(f"Unknown event type: {event_type}")
                    
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected for call {call_sid}")
                break
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON received: {str(e)}")
                continue
            except Exception as e:
                logger.error(f"Error processing message: {str(e)}")
                continue
                
    except Exception as e:
        logger.error(f"Error in Twilio WebSocket connection: {str(e)}", exc_info=True)
    finally:
        connection_manager.disconnect(call_sid)
        logger.info(f"Cleaned up WebSocket connection for call {call_sid}")

@router.websocket("/twilio-elevenlabs-stream/{peer_id}")
async def twilio_elevenlabs_stream_simple(
    websocket: WebSocket,
    peer_id: str
):
    """Simplified WebSocket endpoint for Twilio-ElevenLabs audio streaming"""
    logger.info(f"=== WebSocket connection attempt for peer_id: {peer_id} ===")
    
    company_api_key = "default"
    agent_id = "default"
    
    try:
        result = await twilio_elevenlabs_stream_original(websocket, peer_id, company_api_key, agent_id)
        logger.info(f"WebSocket connection completed for peer_id: {peer_id}")
        return result
    except Exception as e:
        logger.error(f"WebSocket connection failed for peer_id {peer_id}: {str(e)}", exc_info=True)
        raise

@router.websocket("/twilio-elevenlabs-stream/{peer_id}/{company_api_key}/{agent_id}")
async def twilio_elevenlabs_stream_original(
    websocket: WebSocket,
    peer_id: str,
    company_api_key: str,
    agent_id: str
):
    """WebSocket endpoint for Twilio-ElevenLabs audio streaming"""
    try:
        # Extract call SID from peer ID
        if not peer_id.startswith("twilio_"):
            raise HTTPException(status_code=400, detail="Invalid peer ID format")
        
        call_sid = peer_id.split("_")[1] if len(peer_id.split("_")) >= 2 else None
        if not call_sid:
            raise HTTPException(status_code=400, detail="Could not extract call SID from peer ID")
        
        # Check if call exists in integration
        integration_info = await twilio_elevenlabs_integration.get_integration_info(call_sid)
        if not integration_info:
            raise HTTPException(status_code=404, detail="Call not found in integration")
        
        # Accept WebSocket connection first
        await websocket.accept()
        logger.info(f"WebSocket connection accepted for peer {peer_id}")
        
        # Store connection metadata
        metadata = {
            "peer_id": peer_id,
            "call_sid": call_sid,
            "company_api_key": company_api_key,
            "agent_id": agent_id,
            "connection_type": "twilio_elevenlabs",
            "connected_at": datetime.utcnow()
        }
        
        # Connect to connection manager (WebSocket already accepted)
        await connection_manager.connect(websocket, peer_id, metadata)
        
        # Send connection confirmation
        await connection_manager.send_message(peer_id, {
            "type": "connection_established",
            "peer_id": peer_id,
            "call_sid": call_sid,
            "timestamp": datetime.utcnow().isoformat()
        })
        
        logger.info(f"ElevenLabs WebRTC stream connected: {peer_id} for call {call_sid}")
        
        # Handle incoming messages
        try:
            while True:
                data = await websocket.receive_text()
                message = json.loads(data)
                
                message_type = message.get("type")
                
                if message_type == "audio":
                    await handle_incoming_audio(peer_id, message)
                elif message_type == "text":
                    await handle_text_synthesis(peer_id, message)
                elif message_type == "control":
                    await handle_control_message(peer_id, message)
                elif message_type == "ping":
                    await connection_manager.send_message(peer_id, {
                        "type": "pong",
                        "timestamp": datetime.utcnow().isoformat()
                    })
                else:
                    logger.warning(f"Unknown message type: {message_type}")
                    
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected: {peer_id}")
        except Exception as e:
            logger.error(f"Error in WebSocket stream {peer_id}: {str(e)}")
        finally:
            connection_manager.disconnect(peer_id)
            
    except Exception as e:
        logger.error(f"Error establishing WebRTC stream: {str(e)}")
        if websocket.client_state not in (WebSocketState.DISCONNECTED,):
            await websocket.close(code=1011, reason=str(e))

async def handle_twilio_call_start(call_sid: str, stream_sid: str):
    """Handle Twilio call start - synthesize text and send audio"""
    try:
        # Add a small delay to ensure the stream is fully established
        await asyncio.sleep(0.5)
        
        # Generate a greeting using LLM service
        try:
            # Create a simple agent config for greeting
            agent_config = {
                "name": "Customer Service Agent",
                "personality": "friendly and helpful",
                "context": "customer service call"
            }
            text = await llm_service.generate_greeting(agent_config)
            logger.info(f"Generated greeting using LLM: '{text}'")
        except Exception as e:
            logger.error(f"Failed to generate greeting with LLM: {str(e)}")
            # Fallback greeting
            text = "Hello! Thank you for calling. I'm here to help you today. How can I assist you?"
        
        logger.info(f"Synthesizing text for call {call_sid}: {text}")
        
        # Check if the call is in active integrations
        if call_sid not in twilio_elevenlabs_integration.active_integrations:
            logger.error(f"Call {call_sid} not found in active integrations")
            return
        
        # Synthesize text using ElevenLabs integration
        audio_data = await twilio_elevenlabs_integration.synthesize_text_for_call(
            call_sid=call_sid,
            text=text
        )
        
        if audio_data:
            logger.info(f"Synthesized audio: {len(audio_data)} bytes")
            
            # Send audio in chunks to avoid overwhelming the WebSocket
            chunk_size = 8000  # 8KB chunks
            audio_chunks = [audio_data[i:i + chunk_size] for i in range(0, len(audio_data), chunk_size)]
            
            for i, chunk in enumerate(audio_chunks):
                await send_audio_to_twilio_websocket(call_sid, stream_sid, chunk)
                await asyncio.sleep(0.1)  # Small delay between chunks
                logger.debug(f"Sent audio chunk {i+1}/{len(audio_chunks)}")
            
            logger.info(f"Successfully sent all audio chunks to call {call_sid}")
        else:
            logger.error("Failed to synthesize audio")
            
    except Exception as e:
        logger.error(f"Error handling Twilio call start: {str(e)}", exc_info=True)

async def send_audio_to_twilio_websocket(call_sid: str, stream_sid: str, audio_data: bytes):
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
        
        # Send to Twilio via connection manager
        await connection_manager.send_message(call_sid, media_message)
        
        logger.info(f"Sent audio to Twilio for stream {stream_sid}")
        
    except Exception as e:
        logger.error(f"Error sending audio to Twilio: {str(e)}")

async def handle_incoming_audio(peer_id: str, message: Dict[str, Any]):
    """Handle incoming audio from Twilio - process with STT and generate LLM response"""
    try:
        audio_base64 = message.get("data")
        if not audio_base64:
            logger.warning("No audio data in message")
            return
            
        audio_data = base64.b64decode(audio_base64)
        
        metadata = connection_manager.connection_metadata.get(peer_id, {})
        call_sid = metadata.get("call_sid")
        
        if call_sid:
            logger.debug(f"Received audio from Twilio for call {call_sid}: {len(audio_data)} bytes")
            
            # Process audio with STT
            async def transcription_callback(session_id: str, transcribed_text: str):
                """Callback when transcription is complete"""
                if transcribed_text and transcribed_text.strip():
                    logger.info(f"Transcribed speech for call {call_sid}: '{transcribed_text}'")
                    await handle_user_speech(call_sid, transcribed_text)
            
            # Send audio to STT service
            await stt_service.process_audio_chunk(
                session_id=call_sid,
                audio_data=audio_data,
                callback=transcription_callback
            )
            
    except Exception as e:
        logger.error(f"Error handling incoming audio: {str(e)}")

async def handle_user_speech(call_sid: str, user_text: str):
    """Handle user speech - generate LLM response and synthesize with ElevenLabs"""
    try:
        logger.info(f"Processing user speech for call {call_sid}: '{user_text}'")
        
        # Get or create conversation manager for this call
        if call_sid not in conversation_managers:
            # Create a simple agent config for the conversation
            agent_config = {
                "name": "Customer Service Agent",
                "personality": "friendly and helpful",
                "context": "customer service call"
            }
            conversation_managers[call_sid] = ConversationManager(agent_config)
            # Initialize the conversation context
            await conversation_managers[call_sid].start_call(call_sid)
        
        conversation_manager = conversation_managers[call_sid]
        
        # Process user input and get LLM response
        llm_response = await conversation_manager.process_user_input(
            call_sid=call_sid,
            user_input=user_text
        )
        
        if llm_response:
            logger.info(f"Generated LLM response for call {call_sid}: '{llm_response}'")
            
            # Synthesize response with ElevenLabs
            success = await twilio_elevenlabs_integration.synthesize_and_stream(
                call_sid=call_sid,
                text=llm_response
            )
            
            if success:
                logger.info(f"Successfully synthesized and streamed response for call {call_sid}")
            else:
                logger.error(f"Failed to synthesize response for call {call_sid}")
        else:
            logger.warning(f"No LLM response generated for call {call_sid}")
            
    except Exception as e:
        logger.error(f"Error handling user speech for call {call_sid}: {str(e)}", exc_info=True)

async def handle_text_synthesis(peer_id: str, message: Dict[str, Any]):
    """Handle text synthesis request"""
    try:
        text = message.get("text")
        if not text:
            logger.warning("No text in synthesis message")
            return
            
        metadata = connection_manager.connection_metadata.get(peer_id, {})
        call_sid = metadata.get("call_sid")
        
        if call_sid:
            success = await twilio_elevenlabs_integration.synthesize_and_stream(
                call_sid=call_sid,
                text=text
            )
            
            if success:
                await connection_manager.send_message(peer_id, {
                    "type": "synthesis_started",
                    "text": text,
                    "timestamp": datetime.utcnow().isoformat()
                })
            else:
                await connection_manager.send_message(peer_id, {
                    "type": "synthesis_error",
                    "text": text,
                    "error": "Failed to synthesize text",
                    "timestamp": datetime.utcnow().isoformat()
                })
                
    except Exception as e:
        logger.error(f"Error handling text synthesis: {str(e)}")
        await connection_manager.send_message(peer_id, {
            "type": "synthesis_error",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        })

async def handle_control_message(peer_id: str, message: Dict[str, Any]):
    """Handle control messages"""
    try:
        action = message.get("action")
        
        metadata = connection_manager.connection_metadata.get(peer_id, {})
        call_sid = metadata.get("call_sid")
        
        if not call_sid:
            logger.warning("No call SID found for control message")
            return
            
        if action == "stop_synthesis":
            success = await twilio_elevenlabs_integration.stop_call_synthesis(call_sid)
            
            await connection_manager.send_message(peer_id, {
                "type": "control_response",
                "action": action,
                "success": success,
                "timestamp": datetime.utcnow().isoformat()
            })
            
        elif action == "update_voice":
            voice_id = message.get("voice_id")
            voice_settings = message.get("voice_settings")
            
            success = await twilio_elevenlabs_integration.update_call_voice(
                call_sid=call_sid,
                voice_id=voice_id,
                voice_settings=voice_settings
            )
            
            await connection_manager.send_message(peer_id, {
                "type": "control_response",
                "action": action,
                "success": success,
                "timestamp": datetime.utcnow().isoformat()
            })
            
        elif action == "get_call_info":
            info = await twilio_elevenlabs_integration.get_integration_info(call_sid)
            
            await connection_manager.send_message(peer_id, {
                "type": "call_info",
                "info": info,
                "timestamp": datetime.utcnow().isoformat()
            })
            
        else:
            logger.warning(f"Unknown control action: {action}")
            
    except Exception as e:
        logger.error(f"Error handling control message: {str(e)}")
        await connection_manager.send_message(peer_id, {
            "type": "control_error",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        })

@router.get("/connections/{peer_id}/status")
async def get_connection_status(peer_id: str):
    """Get status of a WebRTC connection"""
    try:
        if peer_id in connection_manager.active_connections:
            metadata = connection_manager.connection_metadata.get(peer_id, {})
            return {
                "peer_id": peer_id,
                "status": "connected",
                "metadata": metadata,
                "connected_at": metadata.get("connected_at")
            }
        else:
            return {
                "peer_id": peer_id,
                "status": "disconnected"
            }
    except Exception as e:
        logger.error(f"Error getting connection status: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get connection status")

@router.get("/connections")
async def get_all_connections():
    """Get all active WebRTC connections"""
    try:
        connections = []
        for peer_id, metadata in connection_manager.connection_metadata.items():
            connections.append({
                "peer_id": peer_id,
                "status": "connected",
                "metadata": metadata,
                "connected_at": metadata.get("connected_at")
            })
        return connections
    except Exception as e:
        logger.error(f"Error getting all connections: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get connections")

@router.post("/connections/{peer_id}/disconnect")
async def disconnect_peer(peer_id: str):
    """Disconnect a specific WebRTC connection"""
    try:
        if peer_id in connection_manager.active_connections:
            websocket = connection_manager.active_connections[peer_id]
            await websocket.close(code=1000, reason="Manual disconnect")
            connection_manager.disconnect(peer_id)
            return {"peer_id": peer_id, "status": "disconnected"}
        else:
            raise HTTPException(status_code=404, detail="Connection not found")
    except Exception as e:
        logger.error(f"Error disconnecting peer {peer_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to disconnect")

@router.post("/audio/{peer_id}/send")
async def send_audio_to_peer(peer_id: str, audio_data: bytes):
    """Send audio data to a specific peer"""
    try:
        if peer_id in connection_manager.active_connections:
            await connection_manager.send_audio(peer_id, audio_data)
            return {"peer_id": peer_id, "status": "audio_sent"}
        else:
            raise HTTPException(status_code=404, detail="Connection not found")
    except Exception as e:
        logger.error(f"Error sending audio to peer {peer_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to send audio")