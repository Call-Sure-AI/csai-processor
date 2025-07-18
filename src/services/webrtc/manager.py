# src/services/webrtc/manager.py
from typing import Dict, Set, Optional, Any
import logging
from datetime import datetime
import asyncio
import os
from sqlalchemy.orm import Session
from fastapi import WebSocket

from .peer_connection import PeerConnection
from .audio_handler import WebRTCAudioHandler
from services.vector_store.qdrant_service import QdrantService
from managers.agent_manager import AgentManager
from managers.connection_manager import ConnectionManager
from config.settings import settings
from services.speech.deepgram_ws_service import DeepgramWebSocketService
from services.speech.tts_service import WebSocketTTSService
import base64


import time

logger = logging.getLogger(__name__)

class WebRTCManager:
    def __init__(self):
        self.peers: Dict[str, PeerConnection] = {}  # peer_id -> PeerConnection
        self.company_peers: Dict[str, Set[str]] = {}  # company_id -> set of peer_ids
        self.agent_manager: Optional[AgentManager] = None
        self.connection_manager: Optional[ConnectionManager] = None
        self.vector_store: Optional[QdrantService] = None
        
        # Speech recognition services
        self.speech_services: Dict[str, DeepgramWebSocketService] = {}
        
        # TTS services
        self.tts_services: Dict[str, WebSocketTTSService] = {}
        
        # Transcription buffers
        self.transcripts: Dict[str, str] = {}
        
        # Audio processing flags
        self.is_processing_audio: Dict[str, bool] = {}
        self.response_tasks = {}  
        
        # Initialize audio handler
        audio_save_path = os.path.join(settings.MEDIA_ROOT, 'audio') if hasattr(settings, 'MEDIA_ROOT') else None
        self.audio_handler = WebRTCAudioHandler(audio_save_path=audio_save_path)
        logger.info("WebRTC audio handler initialized")
        
    def initialize_services(self, db: Session, vector_store: QdrantService):
        """Initialize required services"""
        self.vector_store = vector_store
        
        # Initialize agent manager if not already present
        if not self.agent_manager:
            self.agent_manager = AgentManager(db, vector_store)
            logger.info("Agent manager initialized")
            
        # Initialize connection manager if not already present
        if not self.connection_manager:
            self.connection_manager = ConnectionManager(db, vector_store)
            logger.info("Connection manager initialized")
            
    
    async def initialize_speech_service(self, peer_id: str, app=None):
        """Initialize speech recognition service for a peer"""
        if peer_id in self.speech_services:
            return True
            
        try:
            # Create a new Deepgram service
            speech_service = DeepgramWebSocketService()
            
            # Define callback for transcription
            async def transcription_callback(session_id, transcribed_text):
                if not transcribed_text:
                    return
                    
                logger.info(f"Transcription for {peer_id}: '{transcribed_text}'")
                
                # Store the transcript
                self.transcripts[peer_id] = transcribed_text
                
                # Check if we need to process the transcript (if silence detected)
                if app and not self.is_processing_audio.get(peer_id, False):
                    # Check if silence detection task is running
                    if not hasattr(app.state, 'silence_detection_tasks') or peer_id not in app.state.silence_detection_tasks:
                        if not hasattr(app.state, 'silence_detection_tasks'):
                            app.state.silence_detection_tasks = {}
                            
                        # Create and store the task
                        task = asyncio.create_task(self.silence_detection_loop(peer_id, app))
                        app.state.silence_detection_tasks[peer_id] = task
                        logger.info(f"Started silence detection for {peer_id}")
            
            # Initialize the service
            success = await speech_service.initialize_session(peer_id, transcription_callback)
            
            if success:
                self.speech_services[peer_id] = speech_service
                self.transcripts[peer_id] = ""
                self.is_processing_audio[peer_id] = False
                logger.info(f"Initialized speech service for {peer_id}")
                return True
            else:
                logger.error(f"Failed to initialize speech service for {peer_id}")
                return False
                
        except Exception as e:
            logger.error(f"Error initializing speech service: {str(e)}")
            return False
            
    async def silence_detection_loop(self, peer_id: str, app):
        """Loop to detect silence and trigger processing when user stops speaking"""
        # Silence threshold in seconds
        SILENCE_THRESHOLD = 2.0
        last_activity_time = time.time()
        
        try:
            while peer_id in self.peers:
                current_time = time.time()
                
                # Get current transcript
                transcript = self.transcripts.get(peer_id, "")
                
                # Check if we have a new transcript (comparing to last processed time)
                if transcript and transcript.strip():
                    # Update last activity time when we have speech
                    last_activity_time = current_time
                    
                # Check for silence
                silence_duration = current_time - last_activity_time
                
                if silence_duration >= SILENCE_THRESHOLD and transcript and not self.is_processing_audio.get(peer_id, False):
                    logger.info(f"Silence detected for {peer_id}, processing transcript: '{transcript}'")
                    
                    # Mark as processing to avoid duplicate processing
                    self.is_processing_audio[peer_id] = True
                    
                    # Process the transcript
                    try:
                        message_data = {
                            "type": "message",
                            "message": transcript,
                            "source": "audio"
                        }
                        
                        # Clear the transcript
                        self.transcripts[peer_id] = ""
                        
                        # Process the message
                        await self.process_message_with_audio_response(peer_id, message_data, app)
                    except Exception as e:
                        logger.error(f"Error processing transcript: {str(e)}")
                    finally:
                        # Reset processing flag
                        self.is_processing_audio[peer_id] = False
                        # Reset last activity time
                        last_activity_time = time.time()
                
                # Sleep before next check
                await asyncio.sleep(0.1)
                
        except asyncio.CancelledError:
            logger.info(f"Silence detection task cancelled for {peer_id}")
        except Exception as e:
            logger.error(f"Error in silence detection loop: {str(e)}")
        finally:
            logger.info(f"Silence detection ended for {peer_id}")
            self.is_processing_audio[peer_id] = False
            
    # async def process_message_with_audio_response(self, peer_id: str, message_data: dict, app):
    #     """Process a message and respond with both text and audio"""
    #     if peer_id not in self.peers:
    #         logger.warning(f"Client {peer_id} not found")
    #         return
            
    #     peer = self.peers[peer_id]
    #     msg_id = str(time.time())  # Generate a unique message ID
        
    #     try:
    #         # Get connection manager and resources
    #         if not self.connection_manager:
    #             logger.error("Connection manager not initialized")
    #             return
                
    #         # Ensure agent resources are initialized
    #         company_id = peer.company_id
            
    #         # Check if agent resources exist, initialize if not
    #         if peer_id not in self.connection_manager.agent_resources:
    #             # Get agent ID from the peer if available
    #             agent_id = getattr(peer, 'agent_id', None)
                
    #             if not agent_id:
    #                 # Get base agent
    #                 base_agent = await self.agent_manager.get_base_agent(company_id)
    #                 if not base_agent:
    #                     logger.error(f"No base agent found for company {company_id}")
    #                     return
    #                 agent_id = base_agent['id']
                
    #             # Initialize agent resources
    #             agent_info = {'id': agent_id}
    #             success = await self.connection_manager.initialize_agent_resources(
    #                 peer_id, company_id, agent_info
    #             )
                
    #             if not success:
    #                 logger.error(f"Failed to initialize agent resources for {peer_id}")
    #                 return
            
    #         # Get agent resources
    #         agent_res = self.connection_manager.agent_resources.get(peer_id)
    #         if not agent_res:
    #             logger.error(f"No agent resources found for {peer_id}")
    #             return
                
    #         chain = agent_res.get('chain')
    #         rag_service = agent_res.get('rag_service')
            
    #         if not chain or not rag_service:
    #             logger.error(f"Missing chain or rag service for {peer_id}")
    #             return
            
    #         # Initialize TTS service if needed
    #         tts_service = WebSocketTTSService()
            
    #         # Define callback for sending audio back to the client
    #         async def send_audio_to_client(audio_bytes):
    #             try:
    #                 if not hasattr(send_audio_to_client, "chunk_count"):
    #                     send_audio_to_client.chunk_count = 0
                    
    #                 send_audio_to_client.chunk_count += 1
    #                 chunk_number = send_audio_to_client.chunk_count
                    
    #                 # Encode audio data as base64
    #                 encoded_audio = base64.b64encode(audio_bytes).decode("utf-8")
                    
    #                 # Send to client
    #                 await peer.send_message({
    #                     "type": "stream_chunk",
    #                     "text_content": "",  # Empty as this is just audio
    #                     "audio_content": encoded_audio,
    #                     "chunk_number": chunk_number,
    #                     "msg_id": msg_id
    #                 })
                    
    #                 if chunk_number == 1:
    #                     logger.info(f"Sent first audio chunk to client {peer_id}")
                        
    #                 return True
    #             except Exception as e:
    #                 logger.error(f"Error sending audio to client: {str(e)}")
    #                 return False
            
    #         # Start TTS connection
    #         tts_connect_task = asyncio.create_task(tts_service.connect(send_audio_to_client))
            
    #         # Prepare for streaming
    #         full_response_text = ""
    #         current_sentence = ""
    #         chunk_number = 0
            
    #         # Get conversation context if available
    #         conversation = self.connection_manager.client_conversations.get(peer_id)
    #         conversation_context = {}
            
    #         if conversation:
    #             conversation_context = await self.agent_manager.get_conversation_context(conversation['id'])
            
    #         # Wait for TTS connection to be ready
    #         connect_success = await tts_connect_task
            
    #         # Stream the response with audio
    #         async for token in rag_service.get_answer_with_chain(
    #             chain=chain,
    #             question=message_data.get('message', ''),
    #             conversation_context=conversation_context
    #         ):
    #             # Add token to text buffers
    #             full_response_text += token
    #             current_sentence += token
    #             chunk_number += 1
                
    #             # Send text chunk to client
    #             await peer.send_message({
    #                 "type": "stream_chunk",
    #                 "text_content": token,
    #                 "audio_content": None,  # Audio sent separately via callback
    #                 "chunk_number": chunk_number,
    #                 "msg_id": msg_id
    #             })
                
    #             # Process audio by sentence or clause
    #             ends_sentence = any(p in token for p in ".!?")
    #             process_on_comma = "," in token and len(current_sentence) > 40
                
    #             if (ends_sentence or process_on_comma) and current_sentence.strip() and connect_success:
    #                 # Send sentence for TTS conversion
    #                 asyncio.create_task(tts_service.stream_text(current_sentence))
    #                 current_sentence = "" if ends_sentence else ""
                
    #             # Small delay to avoid CPU overload
    #             await asyncio.sleep(0.01)
            
    #         # Process any remaining text
    #         if current_sentence.strip() and connect_success:
    #             await tts_service.stream_text(current_sentence)
            
    #         # Wait for audio to finish
    #         await asyncio.sleep(0.8)
            
    #         # Close TTS connection
    #         if connect_success:
    #             await tts_service.stream_end()
    #             await asyncio.sleep(0.2)
    #             await tts_service.close()
            
    #         # Send end of stream message
    #         await peer.send_message({
    #             "type": "stream_end",
    #             "msg_id": msg_id
    #         })
            
    #         logger.info(f"Completed response for {peer_id}: {full_response_text}")
    #         return full_response_text
            
    #     except Exception as e:
    #         logger.error(f"Error processing message with audio: {str(e)}")
    #         # Cleanup if error occurs
    #         if 'tts_service' in locals() and tts_service is not None:
    #             await tts_service.close()
    #         return None
    
    async def _process_response(self, peer_id: str, peer, msg_id: str, message_data: dict, app):
        try:
            # Ensure agent resources are initialized
            if not self.connection_manager:
                logger.error("Connection manager not initialized")
                return
                    
            company_id = peer.company_id
            
            # Get or initialize agent resources
            if peer_id not in self.connection_manager.agent_resources:
                base_agent = await self.agent_manager.get_base_agent(company_id)
                if not base_agent:
                    logger.error(f"No base agent found for company {company_id}")
                    return
                
                agent_info = {'id': base_agent['id']}
                success = await self.connection_manager.initialize_agent_resources(
                    peer_id, company_id, agent_info
                )
                
                if not success:
                    logger.error(f"Failed to initialize agent resources for {peer_id}")
                    return
            
            # Get agent resources
            agent_res = self.connection_manager.agent_resources.get(peer_id)
            if not agent_res:
                logger.error(f"No agent resources found for {peer_id}")
                return
                    
            chain = agent_res.get('chain')
            rag_service = agent_res.get('rag_service')
            
            if not chain or not rag_service:
                logger.error(f"Missing chain or rag service for {peer_id}")
                return
            
            # Initialize TTS service
            tts_service = WebSocketTTSService()
            
            # Define callback for sending audio back to the client
            async def send_audio_to_client(audio_base64):
                try:
                    await peer.send_message({
                        "type": "stream_chunk",
                        "text_content": "",
                        "audio_content": audio_base64,
                        "msg_id": msg_id
                    })
                    return True
                except Exception as e:
                    logger.error(f"Error sending audio chunk: {str(e)}")
                    return False
            
            # Start TTS connection
            connect_success = await tts_service.connect(send_audio_to_client)
            if not connect_success:
                logger.error("Failed to connect to TTS service")
                return
            
            # Track incoming text and for optimizing audio generation
            accumulated_text = ""
            token_buffer = ""  # To optimize WebSocket traffic
            
            # Get conversation context
            conversation_context = {}
            conversation = self.connection_manager.client_conversations.get(peer_id)
            if conversation:
                conversation_context = await self.agent_manager.get_conversation_context(conversation['id'])
            
            # Stream response tokens and generate audio immediately
            try:
                # Add check for task cancellation
                async for token in rag_service.get_answer_with_chain(
                    chain=chain,
                    question=message_data.get('message', ''),
                    conversation_context=conversation_context
                ):
                    # Check if the task was cancelled
                    if asyncio.current_task().cancelled():
                        logger.info(f"Response generation cancelled during streaming for {peer_id}")
                        break
                    
                    # Accumulate text for logging
                    accumulated_text += token
                    token_buffer += token
                    
                    # Send text chunk to client
                    await peer.send_message({
                        "type": "stream_chunk",
                        "text_content": token,
                        "audio_content": None,
                        "msg_id": msg_id
                    })
                    
                    # Send token to TTS service in small batches
                    if len(token_buffer) >= 3 or any(p in token for p in ".!?,"):
                        await tts_service.stream_text(token_buffer)
                        token_buffer = ""
                    
                    # Small delay to prevent overwhelming the WebSocket
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                logger.info(f"Response streaming cancelled for {peer_id}")
                raise
            
            # Process any remaining buffered tokens
            if token_buffer and not asyncio.current_task().cancelled():
                await tts_service.stream_text(token_buffer)
                
            # Flush any remaining text in ElevenLabs buffer
            if not asyncio.current_task().cancelled():
                await tts_service.flush()
            
            # Wait for audio processing to complete
            await asyncio.sleep(0.8)
            
            # Close TTS service properly
            await tts_service.stream_end()
            await asyncio.sleep(0.2)  # Give time for processing the end signal
            await tts_service.close()
            
            # Send end of stream message if not cancelled
            if not asyncio.current_task().cancelled():
                await peer.send_message({
                    "type": "stream_end",
                    "msg_id": msg_id
                })
            
            logger.info(f"Completed response for {peer_id}: {accumulated_text}")
            return accumulated_text
                
        except asyncio.CancelledError:
            logger.info(f"Response processing cancelled for {peer_id}")
            raise
        except Exception as e:
            logger.error(f"Error processing message with audio: {str(e)}")
            if 'tts_service' in locals() and tts_service is not None:
                await tts_service.close()
            return None
    
    
    async def process_message_with_audio_response(self, peer_id: str, message_data: dict, app):
        """Process a message and respond with streaming text and audio"""
        if peer_id not in self.peers:
            logger.warning(f"Client {peer_id} not found")
            return
                
        peer = self.peers[peer_id]
        msg_id = str(time.time())  # Unique message ID
        
        # Check if there's an ongoing response task for this peer
        if peer_id in self.response_tasks and not self.response_tasks[peer_id].done():
            logger.info(f"Cancelling previous response for {peer_id}")
            # Cancel the previous task
            self.response_tasks[peer_id].cancel()
            try:
                # Wait a brief moment for the task to clean up
                await asyncio.wait_for(self.response_tasks[peer_id], timeout=0.5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            
            # Send a message to the client that the response was interrupted
            try:
                await peer.send_message({
                    "type": "stream_interrupt",
                    "message": "I heard you speaking, let me listen again."
                })
            except Exception as e:
                logger.error(f"Error sending interrupt message: {str(e)}")
        
        # Create a new task for this response
        response_task = asyncio.create_task(self._process_response(peer_id, peer, msg_id, message_data, app))
        self.response_tasks[peer_id] = response_task
        
        try:
            return await response_task
        except asyncio.CancelledError:
            logger.info(f"Response processing cancelled for {peer_id}")
            return None    
            
    async def register_peer(self, peer_id: str, company_info: dict, websocket: WebSocket) -> PeerConnection:
        """Register a new peer connection"""
        company_id = str(company_info['id'])
        
        # Create new peer connection
        peer = PeerConnection(peer_id, company_info)
        await peer.set_websocket(websocket)
        
        # Store peer references
        self.peers[peer_id] = peer
        if company_id not in self.company_peers:
            self.company_peers[company_id] = set()
        self.company_peers[company_id].add(peer_id)
        
        # If this is a client peer (not a WebRTC signaling peer),
        # register with connection manager as well
        if peer_id.startswith('client_') and self.connection_manager:
            await self.connection_manager.connect(websocket, peer_id)
            self.connection_manager.client_companies[peer_id] = company_info
            
        logger.info(f"Registered peer {peer_id} for company {company_id}")
        return peer
        
    async def unregister_peer(self, peer_id: str):
        """Remove a peer connection"""
        if peer_id in self.peers:
            peer = self.peers[peer_id]
            company_id = peer.company_id
            
            # Close any active audio streams for this peer
            try:
                await self.audio_handler.end_audio_stream(peer_id)
            except Exception as e:
                logger.warning(f"Error ending audio stream during peer unregistration: {str(e)}")
            
            # Unregister from connection manager if it's a client peer
            if peer_id.startswith('client_') and self.connection_manager:
                self.connection_manager.disconnect(peer_id)
            
            # Close peer connection
            await peer.close()
            
            # Remove peer references
            del self.peers[peer_id]
            if company_id in self.company_peers:
                self.company_peers[company_id].discard(peer_id)
                if not self.company_peers[company_id]:
                    del self.company_peers[company_id]
                    
            logger.info(f"Unregistered peer {peer_id}")
            
    async def relay_signal(self, from_peer_id: str, to_peer_id: str, signal_data: dict):
        """Relay WebRTC signaling message between peers with special handling for server signals"""
        try:
            # Special handling for signals sent to the server
            if to_peer_id == "server":
                logger.info(f"Processing server-bound signal from {from_peer_id}")
                
                # Get the sender peer
                if from_peer_id in self.peers:
                    from_peer = self.peers[from_peer_id]
                    
                    # Handle offer signal
                    if signal_data.get('type') == 'offer':
                        logger.info(f"Received offer from {from_peer_id}, sending answer")
                        
                        # Create a minimal answer to acknowledge the connection
                        answer = {
                            'type': 'signal',
                            'from_peer': 'server',
                            'data': {
                                'type': 'answer',
                                'sdp': {
                                    'type': 'answer',
                                    'sdp': 'v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=msid-semantic: WMS\r\n'
                                }
                            }
                        }
                        
                        # Send the answer back to the client
                        await from_peer.send_message(answer)
                        
                        # Also send a connection success message
                        await from_peer.send_message({
                            'type': 'connection_success',
                            'message': 'WebRTC signaling completed successfully'
                        })
                        
                        logger.info(f"Sent WebRTC answer to {from_peer_id}")
                        return True
                
                logger.warning(f"Cannot process server signal: peer {from_peer_id} not found")
                return False
            
            # Standard peer-to-peer signal relay
            if to_peer_id in self.peers:
                to_peer = self.peers[to_peer_id]
                try:
                    signal_message = {
                        'type': 'signal',
                        'from_peer': from_peer_id,
                        'data': signal_data
                    }
                    
                    # Send the signal
                    await to_peer.send_message(signal_message)
                    logger.info(f"Successfully relayed signal from {from_peer_id} to {to_peer_id}")
                    return True
                except Exception as e:
                    logger.error(f"Error relaying signal: {str(e)}", exc_info=True)
                    return False
            else:
                logger.warning(f"Cannot relay signal: target peer {to_peer_id} not found")
                return False
        except Exception as e:
            logger.error(f"Error in relay_signal: {str(e)}", exc_info=True)
            return False
            
    async def broadcast_to_company(self, company_id: str, message: dict):
        """Broadcast message to all peers in a company"""
        if company_id in self.company_peers:
            for peer_id in self.company_peers[company_id]:
                if peer_id in self.peers:
                    await self.peers[peer_id].send_message(message)
    
    async def handle_audio_message(self, peer_id: str, message_data: dict) -> dict:
        """Handle audio-related messages"""
        if peer_id not in self.peers:
            logger.warning(f"Audio message received for unknown peer: {peer_id}")
            return {"status": "error", "error": "Unknown peer"}
        
        action = message_data.get("action", "")
        
        if action == "start_stream":
            # Start a new audio stream
            result = await self.audio_handler.start_audio_stream(
                peer_id, message_data.get("metadata", {})
            )
            logger.info(f"Started audio stream for peer {peer_id}: {result}")
            return result
            
        elif action == "audio_chunk":
            # Process an audio chunk
            result = await self.audio_handler.process_audio_chunk(
                peer_id, message_data.get("chunk_data", {})
            )
            # Only log at debug level to avoid log spam
            logger.debug(f"Processed audio chunk for peer {peer_id}")
            return result
            
        elif action == "end_stream":
            # End an audio stream
            result = await self.audio_handler.end_audio_stream(
                peer_id, message_data.get("metadata", {})
            )
            logger.info(f"Ended audio stream for peer {peer_id}: {result}")
            return result
            
        else:
            logger.warning(f"Unknown audio action: {action}")
            return {"status": "error", "error": f"Unknown action: {action}"}
                    
    async def process_message(self, peer_id: str, message_data: dict):
        """Process general messages from peers"""
        if peer_id in self.peers:
            message_type = message_data.get("type", "")
            
            if message_type == "audio":
                # Handle audio-specific messages
                result = await self.handle_audio_message(peer_id, message_data)
                
                # Send result back to the peer
                peer = self.peers[peer_id]
                await peer.send_message({
                    "type": "audio_response",
                    "data": result
                })
                
            elif message_type == "message":
                # Handle streaming text messages through connection manager
                await self.process_streaming_message(peer_id, message_data)
                
    async def process_streaming_message(self, peer_id: str, message_data: dict, agent_id: Optional[str] = None):
        """Process streaming message using ConnectionManager with improved connection handling"""
        try:
            if peer_id not in self.peers:
                logger.warning(f"Message received for unknown peer: {peer_id}")
                return
                
            peer = self.peers[peer_id]
            
            # Check if we have a connection manager
            if not self.connection_manager:
                logger.error("Connection manager not initialized")
                await peer.send_message({
                    "type": "error",
                    "message": "Service not properly initialized"
                })
                return
                
            # Map peer to client ID
            client_id = peer_id
            
            # Initialize agent resources if needed
            company_id = peer.company_id
            
            # Check if the client already has agent resources
            if client_id not in self.connection_manager.agent_resources:
                # Get base agent
                base_agent = {}
                if not agent_id:
                    base_agent = await self.agent_manager.get_base_agent(company_id)
                    if not base_agent:
                        logger.error(f"No base agent found for company {company_id}")
                        await peer.send_message({
                            "type": "error",
                            "message": "No agent available"
                        })
                        return
                else:  
                    logger.info(f"Agent ID: {agent_id}, type: {type(agent_id)}")
                    base_agent = {
                        'id': agent_id
                    }
                    
                # Register WebSocket with connection manager directly
                if peer.websocket and not hasattr(peer.websocket, '_already_registered'):
                    logger.info(f"Registering peer's WebSocket with ConnectionManager for {client_id}")
                    await self.connection_manager.connect(peer.websocket, client_id)
                    self.connection_manager.client_companies[client_id] = peer.company_info
                    setattr(peer.websocket, '_already_registered', True)
                
                success = await self.connection_manager.initialize_agent_resources(
                        client_id,
                        company_id,
                        base_agent
                    )
                
                if not success:
                    logger.error(f"Failed to initialize agent resources for {client_id}")
                    await peer.send_message({
                        "type": "error",
                        "message": "Failed to initialize agent resources"
                    })
                    return
                    
                # Set active agent
                self.connection_manager.active_agents[client_id] = base_agent['id']
                    
            # Process the message using connection manager
            await self.connection_manager.process_streaming_message(client_id, message_data)
            
        except Exception as e:
            logger.error(f"Error processing message stream: {str(e)}", exc_info=True)
            if peer_id in self.peers:
                peer = self.peers[peer_id]
                await peer.send_message({
                    "type": "error",
                    "message": f"Error processing message: {str(e)}"
                })
                        
    def get_company_peers(self, company_id: str) -> list:
        """Get list of active peers for a company"""
        return list(self.company_peers.get(company_id, set()))
        
    def get_stats(self) -> Dict[str, Any]:
        """Get manager statistics"""
        return {
            "total_peers": len(self.peers),
            "total_companies": len(self.company_peers),
            "peers_by_company": {
                company_id: len(peers) 
                for company_id, peers in self.company_peers.items()
            },
            "peer_details": [
                peer.get_stats() for peer in self.peers.values()
            ],
            "audio_stats": self.audio_handler.get_stats()
        }