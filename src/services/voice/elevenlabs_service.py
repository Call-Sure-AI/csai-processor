# src/services/voice/elevenlabs_service.py - COMPLETE VERSION

"""
ElevenLabs Voice Service for Twilio Integration
"""
import asyncio
import logging
import json
import base64
import time
from typing import Dict, Any, Optional, List, Callable, AsyncGenerator
import aiohttp
import httpx
from pydub import AudioSegment
import io
from config.settings import settings
from elevenlabs import ElevenLabs, VoiceSettings

logger = logging.getLogger(__name__)


class ElevenLabsVoiceService:
    """ElevenLabs Voice API service for Twilio integration"""
    
    def __init__(self):
        self.client = ElevenLabs(api_key=settings.eleven_labs_api_key)
        self.api_key = settings.eleven_labs_api_key
        self.voice_id = settings.voice_id
        self.base_url = "https://api.elevenlabs.io/v1"
        self.ws_url = "wss://api.elevenlabs.io/v1/text-to-speech"
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.is_connected = False
        self.is_closed = False
        self.connection_lock = asyncio.Lock()
        self.audio_callback: Optional[Callable] = None
        self.audio_queue = asyncio.Queue()
        self.should_stop_playback = asyncio.Event()
        self.playback_task: Optional[asyncio.Task] = None
        self.listener_task: Optional[asyncio.Task] = None
        self.has_sent_initial_message = False
        self.buffer = ""
        
        self.voice_settings = {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True
        }
        
        # Validate configuration
        if not self.api_key:
            logger.warning("ElevenLabs API key is not set. Voice services will not work.")

    async def generate(self, text: str) -> AsyncGenerator[str, None]:
        """Stream audio chunks in real-time"""
        if not text or not text.strip():
            return
        
        logger.info(f"🎤 Streaming: '{text[:50]}...'")
        
        try:
            loop = asyncio.get_event_loop()
            
            # Stream audio with minimal latency
            audio_generator = self.client.text_to_speech.stream(
                text=text,
                voice_id=self.voice_id,
                model_id="eleven_turbo_v2_5",  # Fastest model
                output_format="ulaw_8000",      # Twilio format
                voice_settings=VoiceSettings(
                    stability=0.65,
                    similarity_boost=0.85,
                    style=0.2,
                    use_speaker_boost=True
                ),
            )
            
            chunk_count = 0
            
            def get_next_chunk():
                try:
                    return next(audio_generator)
                except StopIteration:
                    return None
            
            # Stream chunks immediately as they arrive
            while True:
                chunk = await loop.run_in_executor(None, get_next_chunk)
                if chunk is None:
                    break
                
                if chunk:
                    audio_b64 = base64.b64encode(chunk).decode('ascii')
                    chunk_count += 1
                    yield audio_b64
            
            logger.info(f"✓ Streamed {chunk_count} chunks")
            
        except Exception as e:
            logger.error(f"Streaming error: {str(e)}")
    
    async def initialize(self):
        """Initialize the ElevenLabs service"""
        if not self.api_key:
            logger.error("ElevenLabs API key not configured")
            return False
            
        try:
            if self.session is None or self.session.closed:
                connector = aiohttp.TCPConnector(
                    limit=10,
                    keepalive_timeout=30
                )
                self.session = aiohttp.ClientSession(connector=connector)
            logger.info("ElevenLabs Voice service initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize ElevenLabs service: {str(e)}")
            return False
    
    async def cleanup(self):
        """Cleanup resources"""
        self.is_closed = True
        
        if self.playback_task and not self.playback_task.done():
            self.playback_task.cancel()
            
        if self.listener_task and not self.listener_task.done():
            self.listener_task.cancel()
            
        if self.ws and not self.ws.closed:
            await self.ws.close()
            
        if self.session:
            await self.session.close()
            
        logger.info("ElevenLabs Voice service cleaned up")
    
    async def get_available_voices(self) -> List[Dict[str, Any]]:
        """Get list of available voices"""
        if not self.api_key:
            return []
            
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/voices",
                    headers={"xi-api-key": self.api_key},
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return data.get("voices", [])
                else:
                    logger.error(f"Failed to get voices: {response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error getting voices: {str(e)}")
            return []
    
    async def set_voice(self, voice_id: str):
        """Set the voice ID to use"""
        self.voice_id = voice_id
        logger.info(f"Voice set to: {voice_id}")
    
    async def set_voice_settings(self, settings_dict: Dict[str, Any]):
        """Set voice generation settings"""
        self.voice_settings.update(settings_dict)
        logger.info(f"Voice settings updated: {settings_dict}")
    
    async def connect(self, audio_callback: Callable) -> bool:
        """Connect to ElevenLabs WebSocket API"""
        if not self.api_key:
            logger.error("ElevenLabs API key not configured")
            return False
            
        async with self.connection_lock:
            if self.is_connected and self.ws and not self.ws.closed:
                return True
                
            try:
                # Close existing connection
                if self.ws and not self.ws.closed:
                    await self.ws.close()
                
                # Ensure session exists
                if self.session is None or self.session.closed:
                    await self.initialize()
                
                # Create WebSocket connection
                ws_url = f"{self.ws_url}/{self.voice_id}/stream-input"
                self.ws = await self.session.ws_connect(
                    ws_url,
                    headers={"xi-api-key": self.api_key}
                )
                
                self.is_connected = True
                self.audio_callback = audio_callback
                self.has_sent_initial_message = False
                self.buffer = ""
                
                # Start listener task
                if self.listener_task and not self.listener_task.done():
                    self.listener_task.cancel()
                self.listener_task = asyncio.create_task(self._listen_for_audio())
                
                # Send initial message
                initial_message = {
                    "text": " ",
                    "voice_settings": self.voice_settings,
                    "xi_api_key": self.api_key
                }
                
                await self.ws.send_json(initial_message)
                self.has_sent_initial_message = True
                
                logger.info("Connected to ElevenLabs WebSocket API")
                return True
                
            except Exception as e:
                logger.error(f"Failed to connect to ElevenLabs: {str(e)}")
                self.is_connected = False
                return False
    
    async def disconnect(self):
        """Disconnect from ElevenLabs WebSocket API"""
        async with self.connection_lock:
            self.is_connected = False
            
            if self.ws and not self.ws.closed:
                await self.ws.close()
                
            if self.listener_task and not self.listener_task.done():
                self.listener_task.cancel()
                
            logger.info("Disconnected from ElevenLabs WebSocket API")
    
    async def stop_playback(self):
        """Stop current playback and clear the queue"""
        logger.info("Stopping audio playback and clearing queue")
        self.should_stop_playback.set()
        
        # Clear the queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
            except asyncio.QueueEmpty:
                break
                
        # Send abort message if connected
        if self.is_connected and self.ws and not self.ws.closed:
            try:
                abort_message = {"text": "", "abort": True}
                await self.ws.send_json(abort_message)
                logger.info("Sent abort message to ElevenLabs")
            except Exception as e:
                logger.error(f"Error sending abort message: {str(e)}")
    
    async def stream_text(self, text: str) -> bool:
        """Stream text to ElevenLabs for real-time synthesis"""
        if not text or not text.strip():
            return True
            
        if not self.is_connected or not self.ws or self.ws.closed:
            logger.warning("Not connected to ElevenLabs, attempting to reconnect")
            success = await self.connect(self.audio_callback)
            if not success:
                return False
                        
        if not self.has_sent_initial_message:
            logger.error("Cannot stream text before initial message is sent")
            return False
        
        try:
            # Add text to buffer
            self.buffer += text
            
            # Check if text contains sentence-ending punctuation
            is_complete_chunk = any(p in text for p in ".!?\"")
            
            # Prepare message
            message = {
                "text": text,
                "try_trigger_generation": is_complete_chunk
            }
            
            logger.debug(f"Sending text chunk to ElevenLabs: '{text}'")
            
            # Send message with timeout
            await asyncio.wait_for(
                self.ws.send_json(message), 
                timeout=5.0
            )
            
            # Start playback manager if not running
            if not self.playback_task or self.playback_task.done():
                self.should_stop_playback.clear()
                self.playback_task = asyncio.create_task(self._playback_manager())
            
            return True
                
        except asyncio.TimeoutError:
            logger.error("Timeout sending text to ElevenLabs")
            self.is_connected = False
            return False
        except Exception as e:
            logger.error(f"Error sending text to ElevenLabs: {str(e)}")
            self.is_connected = False
            return False
    
    async def synthesize_text(self, text: str) -> Optional[bytes]:
        """Synthesize text to audio using REST API (for non-streaming use)"""
        if not self.api_key:
            logger.error("ElevenLabs API key not configured")
            return None
            
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/text-to-speech/{self.voice_id}",
                    headers={
                        "xi-api-key": self.api_key,
                        "Content-Type": "application/json"
                    },
                    json={
                        "text": text,
                        "voice_settings": self.voice_settings
                    },
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    return response.content
                else:
                    logger.error(f"ElevenLabs API error: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error synthesizing text: {str(e)}")
            return None
    
    async def _listen_for_audio(self):
        """Listen for audio chunks from ElevenLabs WebSocket"""
        if not self.ws:
            return
            
        try:
            logger.info("Starting ElevenLabs WebSocket audio listener")
            audio_chunks_received = 0
            start_time = time.time()
            
            async for msg in self.ws:
                if self.is_closed:
                    break
                    
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        
                        # Check for audio chunk
                        if "audio" in data:
                            audio_base64 = data["audio"]
                            audio_chunks_received += 1
                            
                            if audio_chunks_received == 1:
                                first_chunk_time = time.time() - start_time
                                logger.info(f"Received first audio chunk in {first_chunk_time:.2f}s")
                            
                            # Decode base64 audio
                            audio_data = base64.b64decode(audio_base64)
                            
                            # Add to queue for playback
                            await self.audio_queue.put(audio_data)
                            
                        # Check for end of stream
                        elif data.get("isFinal"):
                            logger.info(f"Received {audio_chunks_received} audio chunks from ElevenLabs")
                            break
                            
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse WebSocket message: {str(e)}")
                    except Exception as e:
                        logger.error(f"Error processing audio chunk: {str(e)}")
                        
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {msg.data}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("WebSocket connection closed")
                    break
                    
        except asyncio.CancelledError:
            logger.info("ElevenLabs audio listener cancelled")
        except Exception as e:
            logger.error(f"Error in ElevenLabs audio listener: {str(e)}")
        finally:
            logger.info("ElevenLabs audio listener stopped")
    
    async def _playback_manager(self):
        """Manages playback of audio chunks from the queue"""
        logger.info("Starting audio playback manager")
        try:
            while self.is_connected and not self.is_closed:
                # Check if we should stop playback
                if self.should_stop_playback.is_set():
                    logger.info("Playback stopped by request")
                    self.should_stop_playback.clear()
                    # Clear any remaining items in queue
                    while not self.audio_queue.empty():
                        try:
                            self.audio_queue.get_nowait()
                            self.audio_queue.task_done()
                        except asyncio.QueueEmpty:
                            break
                    # Wait for next cycle
                    await asyncio.sleep(0.1)
                    continue
                
                # Check if there's audio in the queue
                if self.audio_queue.empty():
                    # No audio, wait briefly and check again
                    await asyncio.sleep(0.05)
                    continue
                
                # Get the next audio chunk
                try:
                    audio_data = await asyncio.wait_for(self.audio_queue.get(), timeout=0.5)
                    
                    # Send to the callback
                    if self.audio_callback:
                        try:
                            await self.audio_callback(audio_data)
                        except Exception as e:
                            logger.error(f"Error in audio callback: {str(e)}")
                    
                    # Mark task as done
                    self.audio_queue.task_done()
                    
                    # Small delay between chunks for natural speech cadence
                    await asyncio.sleep(0.08)
                    
                except asyncio.TimeoutError:
                    # Timeout is not an error, just continue
                    continue
                except Exception as e:
                    logger.error(f"Error processing audio chunk: {str(e)}")
        
        except asyncio.CancelledError:
            logger.info("Playback manager task cancelled")
        except Exception as e:
            logger.error(f"Error in playback manager: {str(e)}")
        finally:
            logger.info("Playback manager stopped")
    
    async def convert_audio_format(self, audio_data: bytes, target_format: str = "wav") -> bytes:
        """Convert audio data to target format"""
        try:
            # Load audio using pydub
            audio = AudioSegment.from_file(io.BytesIO(audio_data), format="mp3")
            
            # Export to target format
            output = io.BytesIO()
            audio.export(output, format=target_format)
            output.seek(0)
            
            return output.read()
            
        except Exception as e:
            logger.error(f"Error converting audio format: {str(e)}")
            return audio_data
    
    def get_voice_info(self) -> Dict[str, Any]:
        """Get current voice configuration"""
        return {
            "voice_id": self.voice_id,
            "voice_settings": self.voice_settings,
            "is_connected": self.is_connected,
            "has_initial_message": self.has_sent_initial_message
        }


# Global ElevenLabs service instance
elevenlabs_service = ElevenLabsVoiceService()
