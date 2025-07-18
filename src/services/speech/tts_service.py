import aiohttp
import asyncio
import base64
import logging
import json
import time
import os
from typing import Optional, AsyncGenerator, Dict, Any, Callable, List
import io
import wave
import audioop
from pydub import AudioSegment

logger = logging.getLogger(__name__)

class WebSocketTTSService:
    def __init__(self):
        """Initialize WebSocket-based ElevenLabs TTS Service"""
        self.voice_id = os.getenv("VOICE_ID", "IKne3meq5aSn9XLyUdCD")
        self.api_key = os.getenv("ELEVEN_LABS_API_KEY")
        self.base_url = "wss://api.elevenlabs.io/v1/text-to-speech"
        self.session = None
        self.ws = None
        self.audio_callback = None
        self.is_connected = False
        self.is_closed = False
        self.connection_lock = asyncio.Lock()
        self.listener_task = None
        self.has_sent_initial_message = False
        self.buffer = ""
        
        # Audio queue and playback control
        self.audio_queue = asyncio.Queue()
        self.should_stop_playback = asyncio.Event()
        self.playback_task = None
        
        # Validate configuration
        if not self.api_key:
            logger.warning("ElevenLabs API key is not set. TTS services will not work.")
    
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
                
        # Also attempt to abort generation via WebSocket if connected
        if self.is_connected and self.ws and not self.ws.closed:
            try:
                abort_message = {
                    "text": "",
                    "abort": True
                }
                await self.ws.send_json(abort_message)
                logger.info("Sent abort message to ElevenLabs")
            except Exception as e:
                logger.error(f"Error sending abort message: {str(e)}")
                
                
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
                    
                    # Send to the client
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
            
    async def stream_text(self, text: str):
        """Stream text to ElevenLabs following API requirements"""
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
            # Add the text to the buffer
            self.buffer += text
            
            # Check if the text contains sentence-ending punctuation
            is_complete_chunk = any(p in text for p in ".!?\"")
            
            # Prepare message according to ElevenLabs documentation
            message = {
                "text": text,
                "try_trigger_generation": is_complete_chunk
            }
            
            logger.info(f"Sending text chunk to ElevenLabs: '{text}'")
            
            # Send the message with a timeout
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

    async def _listen_for_audio(self):
        """Listen for audio chunks from ElevenLabs"""
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
                            
                            # Queue the audio chunk for playback
                            await self.audio_queue.put(audio_base64)
                           
                        # Handle any errors
                        elif "error" in data:
                            logger.error(f"ElevenLabs API error: {data['error']}")
                            
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON from ElevenLabs")
                    except Exception as e:
                        logger.error(f"Error processing message: {str(e)}")
                
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("ElevenLabs WebSocket closed")
                    break
                    
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {msg.data}")
                    break
            
            logger.info(f"Audio listener summary: {audio_chunks_received} chunks received")
            
        except Exception as e:
            logger.error(f"Critical error in audio listener: {str(e)}")
        finally:
            logger.info("ElevenLabs WebSocket audio listener stopped")

    async def connect(self, audio_callback: Callable[[str], Any] = None):
        """Connect to ElevenLabs WebSocket API"""
        async with self.connection_lock:
            # Reset state
            if self.is_connected and self.ws and not self.ws.closed:
                self.audio_callback = audio_callback
                return True
                
            self.audio_callback = audio_callback
            self.is_closed = False
            self.has_sent_initial_message = False
            self.buffer = ""
            
            try:
                # Construct WebSocket URL with precise parameters per documentation
                url = f"{self.base_url}/{self.voice_id}/stream-input"
                params = {
                    "model_id": "eleven_turbo_v2",
                    "output_format": "mp3_44100",
                    "optimize_streaming_latency": "0",
                    "auto_mode": "false",
                    "inactivity_timeout": "30",
                }
                
                # Add query params to URL
                query_string = "&".join(f"{k}={v}" for k, v in params.items())
                full_url = f"{url}?{query_string}"
                
                logger.info(f"Connecting to ElevenLabs WebSocket")
                
                # Create connection
                self.session = aiohttp.ClientSession()
                headers = {
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json"
                }
                
                # Connect to WebSocket
                self.ws = await self.session.ws_connect(
                    full_url, 
                    headers=headers,
                    heartbeat=30.0,
                    receive_timeout=60.0
                )
                
                # Start listener task
                self.listener_task = asyncio.create_task(self._listen_for_audio())
                
                # Initial connection message - MUST be a space
                initial_message = {
                    "text": " ",  # Required initial message per docs
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.8,
                        "speed": 1.0
                    }
                }
                
                await self.ws.send_json(initial_message)
                self.has_sent_initial_message = True
                self.is_connected = True
                
                logger.info("Connected to ElevenLabs WebSocket API")
                return True
                
            except Exception as e:
                logger.error(f"Error connecting to ElevenLabs WebSocket: {str(e)}")
                await self._cleanup()
                return False
            
    async def stream_end(self):
        """Signal end of stream with empty text"""
        if not self.is_connected:
            return False
            
        try:
            # Check if websocket is still open before sending
            if self.ws and not self.ws.closed:
                # According to docs: End the stream with an empty string
                end_message = {"text": ""}
                await self.ws.send_json(end_message)
                return True
            else:
                logger.info("WebSocket already closed, skipping end signal")
                return False
                
        except Exception as e:
            logger.error(f"Error sending end signal to ElevenLabs: {str(e)}")
            self.is_connected = False
            return False
    
    async def flush(self):
        """Force the generation of audio for accumulated text"""
        if not self.is_connected:
            return False
            
        try:
            # Check if websocket is still open
            if self.ws and not self.ws.closed:
                flush_message = {
                    "text": "",
                    "flush": True  # Force generation of any remaining text
                }
                await self.ws.send_json(flush_message)
                return True
            else:
                logger.info("WebSocket closed, cannot flush")
                return False
        except Exception as e:
            logger.error(f"Error flushing ElevenLabs buffer: {str(e)}")
            return False
            
    async def _cleanup(self):
        """Clean up resources"""
        self.is_connected = False
        
        # Signal playback to stop
        self.should_stop_playback.set()
        
        # Cancel listener task
        if self.listener_task and not self.listener_task.done():
            self.listener_task.cancel()
            try:
                await self.listener_task
            except asyncio.CancelledError:
                pass
            self.listener_task = None
        
        # Cancel playback task
        if self.playback_task and not self.playback_task.done():
            self.playback_task.cancel()
            try:
                await self.playback_task
            except asyncio.CancelledError:
                pass
            self.playback_task = None
        
        # Close WebSocket
        ws = self.ws
        self.ws = None  # Clear reference first
        if ws and not ws.closed:
            try:
                await ws.close()
            except Exception as e:
                logger.error(f"Error closing WebSocket: {str(e)}")
        
        # Close session
        session = self.session
        self.session = None  # Clear reference first
        if session and not session.closed:
            try:
                await session.close()
            except Exception as e:
                logger.error(f"Error closing session: {str(e)}")
    
    async def close(self):
        """Close the WebSocket connection"""
        if self.is_closed:
            return  # Already closed, avoid duplicate close
            
        self.is_closed = True
        
        # Try to send end signal
        try:
            if self.is_connected and self.ws and not self.ws.closed:
                # Send empty text to signal end of conversation
                end_message = {"text": ""}
                await self.ws.send_json(end_message)
                await asyncio.sleep(0.5)  # Allow time for processing
        except Exception as e:
            logger.error(f"Error during stream end: {str(e)}")
        
        # Clean up resources
        await self._cleanup()
        logger.info("Closed ElevenLabs WebSocket connection")


async def convert_mp3_to_mulaw(mp3_base64):
    """Convert MP3 audio from ElevenLabs to Twilio's required mulaw format"""
    try:
        # Decode base64 to binary
        mp3_data = base64.b64decode(mp3_base64)
        
        # Convert MP3 to WAV (16-bit PCM)
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_data))
        
        # Resample to 8000 Hz mono (Twilio's required format)
        audio = audio.set_frame_rate(8000).set_channels(1)
        
        # Convert to raw PCM
        pcm_data = audio.raw_data
        
        # Convert to mulaw (Twilio's required encoding)
        mulaw_data = audioop.lin2ulaw(pcm_data, 2)  # 2 bytes per sample (16-bit)
        
        # Convert back to base64
        mulaw_base64 = base64.b64encode(mulaw_data).decode('utf-8')
        
        return mulaw_base64
    except Exception as e:
        logger.error(f"Error converting MP3 to mulaw: {str(e)}")
        return None