import asyncio
import json
import base64
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum
import numpy as np
import websockets
import logging

logger = logging.getLogger(__name__)

class StreamState(Enum):
    CONNECTED = "connected"
    STREAMING = "streaming"
    PAUSED = "paused"
    DISCONNECTED = "disconnected"

@dataclass
class AudioBuffer:
    """Manages audio buffering for smooth processing"""
    sample_rate: int = 8000  # Twilio uses 8kHz
    chunk_duration_ms: int = 20  # Twilio sends 20ms chunks
    
    def __init__(self):
        self.buffer = bytearray()
        self.timestamps = []
        
    def add_chunk(self, audio_data: bytes, timestamp: int):
        """Add audio chunk to buffer"""
        self.buffer.extend(audio_data)
        self.timestamps.append(timestamp)
        
    def get_chunks(self, duration_ms: int) -> Optional[bytes]:
        """Get audio chunks of specified duration"""
        chunk_size = int(self.sample_rate * duration_ms / 1000 * 2)  # 16-bit audio
        if len(self.buffer) >= chunk_size:
            chunk = bytes(self.buffer[:chunk_size])
            self.buffer = self.buffer[chunk_size:]
            return chunk
        return None
        
    def clear(self):
        """Clear the buffer"""
        self.buffer.clear()
        self.timestamps.clear()

class TwilioMediaStreamHandler:
    """Handles bidirectional media streaming with Twilio"""
    
    def __init__(self, call_sid: str, stream_sid: str):
        self.call_sid = call_sid
        self.stream_sid = stream_sid
        self.state = StreamState.DISCONNECTED
        self.websocket: Optional[websockets.WebSocketServerProtocol] = None
        self.audio_buffer = AudioBuffer()
        self.outbound_queue = asyncio.Queue()
        self.stt_handler = None
        self.tts_handler = None
        self.conversation_manager = None
        self.is_agent_speaking = False
        self.user_speaking_start = None
        self.silence_threshold_ms = 1500  # Wait 1.5s of silence before processing
        
    async def handle_websocket(self, websocket, path):
        """Main WebSocket handler for Twilio media stream"""
        self.websocket = websocket
        self.state = StreamState.CONNECTED
        logger.info(f"WebSocket connected for call {self.call_sid}")
        
        try:
            # Start concurrent tasks
            tasks = [
                asyncio.create_task(self._receive_media()),
                asyncio.create_task(self._send_media()),
                asyncio.create_task(self._process_audio())
            ]
            
            await asyncio.gather(*tasks)
            
        except Exception as e:
            logger.error(f"WebSocket error for call {self.call_sid}: {str(e)}")
        finally:
            self.state = StreamState.DISCONNECTED
            await self.cleanup()
            
    async def _receive_media(self):
        """Receive and process media from Twilio"""
        async for message in self.websocket:
            try:
                data = json.loads(message)
                
                if data['event'] == 'start':
                    self.state = StreamState.STREAMING
                    await self._handle_stream_start(data['start'])
                    
                elif data['event'] == 'media':
                    await self._handle_media(data['media'])
                    
                elif data['event'] == 'stop':
                    logger.info(f"Stream stopped for call {self.call_sid}")
                    break
                    
            except Exception as e:
                logger.error(f"Error processing message: {str(e)}")
                
    async def _handle_stream_start(self, start_data: Dict):
        """Handle stream start event"""
        self.stream_sid = start_data.get('streamSid')
        logger.info(f"Stream started: {self.stream_sid}")
        
        # Initialize STT and conversation components
        if self.conversation_manager:
            await self.conversation_manager.start_call(self.call_sid)
            
    async def _handle_media(self, media_data: Dict):
        """Handle incoming audio media"""
        if self.is_agent_speaking:
            # Ignore user audio while agent is speaking (prevent interruption)
            return
            
        # Decode audio from base64
        audio_payload = base64.b64decode(media_data['payload'])
        timestamp = int(media_data.get('timestamp', 0))
        
        # Add to buffer
        self.audio_buffer.add_chunk(audio_payload, timestamp)
        
    async def _process_audio(self):
        """Process buffered audio for STT"""
        while self.state == StreamState.STREAMING:
            # Get 100ms chunks for processing
            audio_chunk = self.audio_buffer.get_chunks(100)
            
            if audio_chunk and self.stt_handler:
                # Send to STT engine
                text_result = await self.stt_handler.process_audio(audio_chunk)
                
                if text_result:
                    await self._handle_user_speech(text_result)
                    
            await asyncio.sleep(0.05)  # 50ms loop
            
    async def _handle_user_speech(self, text: str):
        """Process user speech and generate response"""
        if not self.conversation_manager:
            return
            
        # Get AI response
        response = await self.conversation_manager.process_user_input(
            call_sid=self.call_sid,
            user_input=text
        )
        
        if response:
            await self.speak_response(response)
            
    async def speak_response(self, text: str):
        """Convert text to speech and send to caller"""
        if not self.tts_handler:
            return
            
        self.is_agent_speaking = True
        
        try:
            # Generate audio from text
            audio_data = await self.tts_handler.synthesize(text)
            
            # Send audio in chunks
            await self._stream_audio_to_caller(audio_data)
            
        finally:
            self.is_agent_speaking = False
            
    async def _stream_audio_to_caller(self, audio_data: bytes):
        """Stream audio data to Twilio"""
        # Twilio expects 8kHz, 16-bit PCM, base64 encoded
        chunk_size = 320  # 20ms at 8kHz
        
        for i in range(0, len(audio_data), chunk_size):
            chunk = audio_data[i:i + chunk_size]
            
            # Pad if necessary
            if len(chunk) < chunk_size:
                chunk = chunk + b'\x00' * (chunk_size - len(chunk))
                
            # Encode and send
            encoded = base64.b64encode(chunk).decode('utf-8')
            
            message = {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {
                    "payload": encoded
                }
            }
            
            await self.websocket.send(json.dumps(message))
            await asyncio.sleep(0.02)  # 20ms pacing
            
    async def _send_media(self):
        """Send queued media to Twilio"""
        while self.state == StreamState.STREAMING:
            try:
                message = await asyncio.wait_for(
                    self.outbound_queue.get(), 
                    timeout=0.1
                )
                await self.websocket.send(message)
            except asyncio.TimeoutError:
                continue
                
    async def cleanup(self):
        """Clean up resources"""
        self.audio_buffer.clear()
        if self.conversation_manager:
            await self.conversation_manager.end_call(self.call_sid)


twilio_media_stream_handler = TwilioMediaStreamHandler()