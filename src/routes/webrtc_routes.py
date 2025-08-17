"""
WebRTC Routes for CSAI Processor
Migrated from cs_ai_backend with optimizations
"""
from fastapi import APIRouter, WebSocket, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
import logging
import json
from datetime import datetime
import asyncio
import time
import uuid
import base64

from database.config import get_db
from database.models import Company, Agent
from managers.connection_manager import ConnectionManager
from managers.webrtc_manager import WebRTCManager
from services.vector_store.qdrant_service import QdrantService
from services.speech.stt_service import SpeechToTextService
from services.speech.tts_service import WebSocketTTSService
from services.speech.deepgram_ws_service import DeepgramWebSocketService
from config.settings import settings

# Initialize router and logging
router = APIRouter()
logger = logging.getLogger(__name__)

# Global instances (will be initialized in startup)
webrtc_manager: WebRTCManager = None
vector_store: QdrantService = None
connection_manager: ConnectionManager = None

# Audio processing state
audio_buffers = {}
last_speech_timestamps = {}
is_processing = {}
SILENCE_THRESHOLD = 2.0


def initialize_webrtc_services(
    webrtc_mgr: WebRTCManager,
    vector_store_instance: QdrantService,
    conn_manager: ConnectionManager
):
    """Initialize WebRTC services with dependencies"""
    global webrtc_manager, vector_store, connection_manager
    webrtc_manager = webrtc_mgr
    vector_store = vector_store_instance
    connection_manager = conn_manager
    logger.info("WebRTC services initialized")


async def initialize_webrtc_manager(manager: WebRTCManager):
    """Initialize WebRTC manager with required services"""
    try:
        if not hasattr(manager, 'speech_services'):
            manager.speech_services = {}
        if not hasattr(manager, 'audio_handler'):
            manager.audio_handler = {}
        logger.info("WebRTC manager initialized")
    except Exception as e:
        logger.error(f"Error initializing WebRTC manager: {str(e)}")


async def handle_audio_stream(peer_id: str, audio_data: bytes, manager: WebRTCManager, app):
    """Handle incoming audio stream with optimized processing"""
    try:
        # Initialize speech service if needed
        if peer_id not in manager.speech_services:
            speech_service = DeepgramWebSocketService()
            
            async def transcription_callback(session_id, transcribed_text):
                # Store transcript in app state
                if hasattr(app.state, 'transcripts'):
                    app.state.transcripts[peer_id] = transcribed_text
                else:
                    app.state.transcripts = {peer_id: transcribed_text}
            
            success = await speech_service.initialize_session(peer_id, transcription_callback)
            if success:
                manager.speech_services[peer_id] = speech_service
        
        # Process audio chunk
        speech_service = manager.speech_services.get(peer_id)
        if speech_service:
            await speech_service.process_audio_chunk(peer_id, audio_data)
        
        # Start silence detection if not running
        if not hasattr(app.state, 'silence_detection_tasks') or peer_id not in app.state.silence_detection_tasks:
            if not hasattr(app.state, 'silence_detection_tasks'):
                app.state.silence_detection_tasks = {}
            task = asyncio.create_task(silence_detection_loop(peer_id, manager, app))
            app.state.silence_detection_tasks[peer_id] = task
            
    except Exception as e:
        logger.error(f"Error handling audio stream for {peer_id}: {str(e)}")


async def silence_detection_loop(peer_id: str, manager: WebRTCManager, app):
    """Optimized silence detection loop"""
    try:
        while True:
            await asyncio.sleep(0.5)  # Check every 500ms instead of continuous loop
            
            current_time = time.time()
            last_speech = last_speech_timestamps.get(peer_id, 0)
            
            if current_time - last_speech > SILENCE_THRESHOLD:
                if peer_id in is_processing and not is_processing[peer_id]:
                    is_processing[peer_id] = True
                    
                    # Process accumulated audio
                    audio_buffer = audio_buffers.get(peer_id, b'')
                    if audio_buffer:
                        await process_accumulated_audio(peer_id, audio_buffer, manager, app)
                        audio_buffers[peer_id] = b''
                    
                    is_processing[peer_id] = False
                    
    except asyncio.CancelledError:
        logger.info(f"Silence detection loop cancelled for {peer_id}")
    except Exception as e:
        logger.error(f"Error in silence detection loop for {peer_id}: {str(e)}")


async def process_accumulated_audio(peer_id: str, audio_data: bytes, manager: WebRTCManager, app):
    """Process accumulated audio data"""
    try:
        # Get transcript from app state
        transcript = getattr(app.state, 'transcripts', {}).get(peer_id, '')
        if transcript:
            # Process with connection manager
            if connection_manager:
                await connection_manager.process_audio_transcript(peer_id, transcript)
            
            # Clear transcript
            if hasattr(app.state, 'transcripts'):
                app.state.transcripts[peer_id] = ''
                
    except Exception as e:
        logger.error(f"Error processing accumulated audio for {peer_id}: {str(e)}")


async def process_webrtc_message(websocket: WebSocket, peer_id: str, message_data: dict, webrtc_manager: WebRTCManager, connection_manager: ConnectionManager):
    """Process a message and respond with streaming text through WebRTC WebSocket"""
    try:
        logger.info(f"Processing WebRTC message for {peer_id}: {message_data.get('message', '')[:50]}...")
        
        # Get the peer from WebRTC manager
        if peer_id not in webrtc_manager.peers:
            logger.warning(f"Peer {peer_id} not found in WebRTC manager")
            return
        
        peer = webrtc_manager.peers[peer_id]
        msg_id = str(int(time.time() * 1000))  # Unique message ID
        
        # Get agent resources from connection manager
        agent_res = connection_manager.agent_resources.get(peer_id)
        if not agent_res:
            logger.warning(f"No agent resources found for {peer_id} - sending simple response")
            # Send a simple response when agent resources are not available
            simple_response = f"I received your message: '{message_data.get('message', '')}'. Agent resources are not currently available, but the WebRTC connection is working."
            await websocket.send_json({
                "type": "stream_chunk",
                "text_content": simple_response,
                "msg_id": msg_id
            })
            await websocket.send_json({
                "type": "stream_end",
                "msg_id": msg_id,
                "full_response": simple_response
            })
            return
        
        chain = agent_res.get('chain')
        rag_service = agent_res.get('rag_service')
        
        if not chain or not rag_service:
            logger.error(f"Missing chain or rag service for {peer_id}")
            await websocket.send_json({
                "type": "error",
                "message": "Agent services not available",
                "msg_id": msg_id
            })
            return
        
        # Get conversation context
        conversation_context = {}
        conversation = connection_manager.client_conversations.get(peer_id)
        if conversation:
            conversation_context = await connection_manager.agent_manager.get_conversation_context(conversation['id'])
        
        # Stream response tokens
        full_response = ""
        current_sentence = ""
        
        logger.info(f"Starting response generation for {peer_id}")
        
        # Stream response with sentence-by-sentence processing
        async for token in rag_service.get_answer_with_chain(
            chain=chain,
            question=message_data.get('message', ''),
            conversation_context=conversation_context
        ):
            # Add token to buffers
            full_response += token
            current_sentence += token
            
            # Send text token to client immediately
            try:
                await websocket.send_json({
                    "type": "stream_chunk",
                    "text_content": token,
                    "msg_id": msg_id
                })
            except Exception as e:
                logger.error(f"Error sending stream chunk: {str(e)}")
                break
            
            # Check for sentence end
            if any(p in token for p in ".!?"):
                current_sentence = ""
            
            # Small delay to avoid overwhelming the client
            await asyncio.sleep(0.01)
        
        # Send stream end message
        try:
            await websocket.send_json({
                "type": "stream_end",
                "msg_id": msg_id,
                "full_response": full_response
            })
        except Exception as e:
            logger.error(f"Error sending stream end: {str(e)}")
        
        logger.info(f"Completed response for {peer_id}: {full_response[:100]}...")
        
    except Exception as e:
        logger.error(f"Error processing WebRTC message for {peer_id}: {str(e)}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Error processing message: {str(e)}",
                "msg_id": msg_id if 'msg_id' in locals() else "unknown"
            })
        except Exception as send_error:
            logger.error(f"Error sending error response: {str(send_error)}")


async def initialize_client_resources(websocket: WebSocket, peer_id: str, company_id: str, agent_id: str, db: Session):
    """Initialize client-specific resources"""
    try:
        # For testing purposes, skip agent validation if using test agent
        logger.info(f"========================================================")
        logger.info(f"Initializing client resources for {peer_id} with company_id: {company_id} and agent_id: {agent_id}")
        logger.info(f"========================================================")


        # TODO: Uncomment this when we have a proper agent validation
        # if agent_id == "test_agent_123":
        #     logger.info(f"Using test agent: {agent_id}")
        # else:
        #     # Validate agent
        #     agent = db.query(Agent).filter_by(id=agent_id, company_id=company_id).first()
        #     logger.info(f"Agent: {agent}")
        #     if not agent:
        #         logger.error(f"Agent {agent_id} not found for company {company_id}")
        #         return False
        
        # Initialize audio buffer
        audio_buffers[peer_id] = b''
        last_speech_timestamps[peer_id] = time.time()
        is_processing[peer_id] = False
        
        logger.info(f"Client resources initialized for {peer_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error initializing client resources: {str(e)}")
        return False


@router.websocket("/signal/{peer_id}/{company_api_key}/{agent_id}")
async def signaling_endpoint(
    websocket: WebSocket,
    peer_id: str,
    company_api_key: str,
    agent_id: str,
    db: Session = Depends(get_db)
):
    """Optimized WebRTC signaling endpoint"""
    connection_start = time.time()
    websocket_closed = False
    peer = None
    is_twilio_client = peer_id.startswith('twilio_')
    
    try:
        # Initialize WebRTC manager
        if webrtc_manager:
            await initialize_webrtc_manager(webrtc_manager)
        
        # Handle Twilio clients differently
        if is_twilio_client:
            logger.info(f"Twilio client connected: {peer_id}")
            await websocket.accept()
            await handle_twilio_media_stream(websocket, peer_id, company_api_key, agent_id, db)
            return
        
        # Validate company for regular clients
        company = db.query(Company).filter_by(api_key=company_api_key).first()

        print(f"Company: {company}")
        
        # For testing purposes, create a mock company if not found
        if not company:
            if company_api_key == "test_api_key_123":
                logger.info(f"Using test company for API key: {company_api_key}")
                # Create a mock company for testing
                from uuid import uuid4
                company = type('MockCompany', (), {
                    'id': str(uuid4()),
                    'name': 'Test Company',
                    'settings': {}
                })()
            else:
                logger.warning(f"Invalid API key: {company_api_key}")
                await websocket.close(code=4001)
                return
        
        # Accept connection
        await websocket.accept()
        await websocket.send_json({
            "type": "connection_ack",
            "status": "success",
            "peer_id": peer_id
        })
        
        # Register peer
        if webrtc_manager:
            company_info = {
                "id": company.id,
                "name": company.name,
                "settings": company.settings
            }
            peer = await webrtc_manager.register_peer(peer_id, company_info, websocket)
        
        # Initialize client resources
        await initialize_client_resources(websocket, peer_id, str(company.id), agent_id, db)
        
        # Initialize agent resources for WebRTC (optional - skip if vector store unavailable)
        if connection_manager:
            logger.info(f"Initializing agent resources for {peer_id} with company_id: {company.id} and agent_id: {agent_id}")
            try:
                # Get agent info
                agent = db.query(Agent).filter_by(id=agent_id, company_id=str(company.id)).first()
                if agent:
                    agent_info = {
                        "id": agent.id,
                        "name": agent.name,
                        "type": agent.type,
                        "prompt": agent.prompt,
                        "confidence_threshold": agent.confidence_threshold,
                        "additional_context": agent.additional_context
                    }
                    
                    # Initialize agent resources
                    success = await connection_manager.initialize_agent_resources(peer_id, str(company.id), agent_info)
                    if success:
                        logger.info(f"Agent resources initialized for {peer_id}")
                    else:
                        logger.warning(f"Failed to initialize agent resources for {peer_id} - continuing without RAG")
                else:
                    logger.warning(f"Agent {agent_id} not found for company {company.id} - continuing without agent")
            except Exception as e:
                logger.warning(f"Error initializing agent resources for {peer_id}: {str(e)} - continuing without RAG")
                # Continue without agent resources - WebRTC will still work for basic messaging
        
        # Main message handling loop
        while True:
            try:
                data = await websocket.receive_json()
                message_type = data.get('type', 'unknown')

                logger.info(f"Message type: {message_type}")
                logger.info(f"Data: {data}")

                if message_type == 'config_request':
                    # Send ICE configuration
                    config_message = {
                        'type': 'config',
                        'ice_servers': [
                            {'urls': ['stun:stun.l.google.com:19302']},
                            # Add TURN servers here for production
                        ]
                    }
                    await websocket.send_json(config_message)
                    logger.info(f"ICE config sent to {peer_id}")
                    
                elif message_type == 'signal':
                    # Handle signaling messages
                    to_peer = data.get('to_peer')
                    if webrtc_manager:
                        await webrtc_manager.relay_signal(peer_id, to_peer, data.get('data', {}))
                        
                elif message_type == 'audio':
                    # Handle audio messages
                    audio_data = data.get('audio_data', '')
                    if audio_data:
                        # Decode base64 audio
                        try:
                            audio_bytes = base64.b64decode(audio_data)
                            await handle_audio_stream(peer_id, audio_bytes, webrtc_manager, websocket.app)
                            last_speech_timestamps[peer_id] = time.time()
                            
                            # Send acknowledgment
                            await websocket.send_json({
                                "type": "audio_ack",
                                "status": "received"
                            })
                        except Exception as e:
                            logger.error(f"Error processing audio for {peer_id}: {str(e)}")
                            
                elif message_type == 'message':
                    # Handle text messages with WebRTC response
                    await process_webrtc_message(websocket, peer_id, data, webrtc_manager, connection_manager)
                        
                elif message_type == 'ping':
                    # Handle heartbeat
                    await websocket.send_json({"type": "pong"})
                    
            except Exception as e:
                logger.error(f"Error processing message for {peer_id}: {str(e)}")
                break
                
    except Exception as e:
        logger.error(f"Error in signaling endpoint: {str(e)}")
    finally:
        # Cleanup
        try:
            # Clean up speech services
            if webrtc_manager and hasattr(webrtc_manager, 'speech_services'):
                speech_service = webrtc_manager.speech_services.pop(peer_id, None)
                if speech_service:
                    await speech_service.close_session(peer_id)
            
            # Clean up silence detection tasks
            if hasattr(websocket.app.state, 'silence_detection_tasks'):
                task = websocket.app.state.silence_detection_tasks.pop(peer_id, None)
                if task and not task.done():
                    task.cancel()
            
            # Clean up audio buffers
            audio_buffers.pop(peer_id, None)
            last_speech_timestamps.pop(peer_id, None)
            is_processing.pop(peer_id, None)
            
            # Unregister peer
            if webrtc_manager:
                await webrtc_manager.unregister_peer(peer_id)
            
            total_time = time.time() - connection_start
            logger.info(f"Connection ended for peer {peer_id}. Duration: {total_time:.3f}s")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")


async def handle_twilio_media_stream(websocket: WebSocket, peer_id: str, company_api_key: str, agent_id: str, db: Session):
    """Handle Twilio media stream with optimized processing"""
    try:
        # Initialize speech service for Twilio
        speech_service = DeepgramWebSocketService()
        
        async def transcription_callback(session_id, transcribed_text):
            # Process transcript immediately for Twilio
            if connection_manager:
                await connection_manager.process_audio_transcript(peer_id, transcribed_text)
        
        success = await speech_service.initialize_session(peer_id, transcription_callback)
        if not success:
            logger.error(f"Failed to initialize speech service for {peer_id}")
            return
        
        # Handle Twilio media stream
        while True:
            try:
                data = await websocket.receive_json()
                
                if data.get('event') == 'media':
                    # Process Twilio media
                    media_data = data.get('media', {})
                    audio_payload = media_data.get('payload', '')
                    
                    if audio_payload:
                        audio_bytes = base64.b64decode(audio_payload)
                        await speech_service.process_audio_chunk(peer_id, audio_bytes)
                        
                elif data.get('event') == 'stop':
                    logger.info(f"Twilio stream stopped for {peer_id}")
                    break
                    
            except Exception as e:
                logger.error(f"Error processing Twilio media for {peer_id}: {str(e)}")
                break
                
    except Exception as e:
        logger.error(f"Error in Twilio media stream handler: {str(e)}")
    finally:
        # Cleanup Twilio resources
        if 'speech_service' in locals():
            await speech_service.close_session(peer_id)


@router.get("/audio/streams/{company_api_key}")
async def get_audio_streams(company_api_key: str, db: Session = Depends(get_db)):
    """Get active audio streams for a company"""
    try:
        company = db.query(Company).filter_by(api_key=company_api_key).first()
        if not company:
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        company_id = str(company.id)
        active_peers = webrtc_manager.get_company_peers(company_id) if webrtc_manager else []
        
        # Collect stream info
        stream_info = {}
        for peer_id in active_peers:
            if peer_id in audio_buffers:
                stream_info[peer_id] = {
                    "is_active": True,
                    "last_activity": last_speech_timestamps.get(peer_id, 0)
                }
        
        return {
            "company_id": company_id,
            "active_audio_streams": len(stream_info),
            "streams": stream_info
        }
        
    except Exception as e:
        logger.error(f"Error getting audio streams: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/audio/stats")
async def get_audio_stats():
    """Get audio processing statistics"""
    try:
        return {
            "active_connections": len(audio_buffers),
            "processing_flags": sum(is_processing.values()),
            "silence_threshold": SILENCE_THRESHOLD,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting audio stats: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/peers/{company_api_key}")
async def get_company_peers(company_api_key: str, db: Session = Depends(get_db)):
    """Get active peers for a company"""
    try:
        company = db.query(Company).filter_by(api_key=company_api_key).first()
        if not company:
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        company_id = str(company.id)
        active_peers = webrtc_manager.get_company_peers(company_id) if webrtc_manager else []
        
        return {
            "company_id": company_id,
            "active_peers": active_peers,
            "peer_count": len(active_peers)
        }
        
    except Exception as e:
        logger.error(f"Error getting company peers: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/stats")
async def get_webrtc_stats():
    """Get comprehensive WebRTC statistics"""
    try:
        stats = {
            "total_peers": len(audio_buffers),
            "active_connections": len([p for p in is_processing.values() if p]),
            "audio_buffers": len(audio_buffers),
            "speech_services": len(webrtc_manager.speech_services) if webrtc_manager else 0,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        if webrtc_manager:
            stats.update(webrtc_manager.get_stats())
        
        return stats
        
    except Exception as e:
        logger.error(f"Error getting WebRTC stats: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.websocket("/twilio-stream/{peer_id}/{company_api_key}/{agent_id}")
async def handle_twilio_media_stream(
    websocket: WebSocket,
    peer_id: str,
    company_api_key: str,
    agent_id: str
):
    """Handle Twilio Media Streams with WebRTC integration"""
    connection_id = str(uuid.uuid4())[:8]
    connected = False
    
    try:
        logger.info(f"[{connection_id}] Handling Twilio media stream for {peer_id}")
        
        # Accept WebSocket connection
        await websocket.accept()
        connected = True
        
        # Initialize services
        db = next(get_db())
        company = db.query(Company).filter_by(api_key=company_api_key).first()
        if not company:
            logger.error(f"[{connection_id}] Company not found")
            await websocket.close(code=1011)
            return
            
        # Initialize speech services
        speech_service = DeepgramWebSocketService()
        tts_service = WebSocketTTSService()
        
        # Register with connection manager
        if connection_manager:
            company_info = {"id": company.id, "name": company.name}
            connection_manager.client_companies[peer_id] = company_info
            await connection_manager.connect(websocket, peer_id)
            
        # Process audio stream
        async for message in websocket.iter_text():
            try:
                data = json.loads(message)
                
                if data.get("event") == "media":
                    # Handle Twilio media payload
                    media_payload = data.get("media", {})
                    audio_data = base64.b64decode(media_payload.get("payload", ""))
                    
                    # Process with speech recognition
                    await speech_service.process_audio_chunk(peer_id, audio_data)
                    
                elif data.get("event") == "stop":
                    logger.info(f"[{connection_id}] Twilio stream ended for {peer_id}")
                    break
                    
            except json.JSONDecodeError:
                logger.warning(f"[{connection_id}] Invalid JSON received")
                continue
                
    except Exception as e:
        logger.error(f"[{connection_id}] Error in Twilio stream: {str(e)}")
    finally:
        if connected:
            await websocket.close()
        logger.info(f"[{connection_id}] Twilio stream closed for {peer_id}")
