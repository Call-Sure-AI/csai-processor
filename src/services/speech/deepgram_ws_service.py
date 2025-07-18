import asyncio
import json
import logging
import os
import time
import websockets
from typing import Dict, Callable, Awaitable, Optional
import random
import base64
import audioop


logger = logging.getLogger(__name__)

class DeepgramWebSocketService:
    def __init__(self):
        self.sessions: Dict[str, Dict] = {}
        self.deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
        self.ws_base_url = "wss://api.deepgram.com/v1/listen"

    async def initialize_session(self, session_id: str, callback: Callable[[str, str], Awaitable[None]]) -> bool:
        try:
            if session_id in self.sessions:
                logger.info(f"Closing existing session for {session_id}")
                await self.close_session(session_id)

            # Check for API key
            if not self.deepgram_api_key:
                logger.error(f"No Deepgram API key found for session {session_id}")
                return False

            query_params = [
                "model=nova-3",
                "endpointing=true",
                "punctuate=true",
                "smart_format=true",
                "filler_words=false",
                "interim_results=false",
                "encoding=linear16",
                "sample_rate=16000"
            ]

            url = f"{self.ws_base_url}?{'&'.join(query_params)}"
            logger.info(f"Connecting to Deepgram for session {session_id}: {url}")
            
            headers = {"Authorization": f"Token {self.deepgram_api_key}"}

            session = {
                "websocket": None,
                "connected": False,
                "callback": callback,
                "task": None,
            }
            self.sessions[session_id] = session

            session["task"] = asyncio.create_task(self._session_handler(session_id, url, headers))

            # Wait for connection to be established
            for i in range(50):  # 5 seconds max wait
                if session_id not in self.sessions:
                    logger.error(f"Session {session_id} was removed during initialization")
                    return False
                    
                if self.sessions[session_id]["connected"]:
                    logger.info(f"Connected to Deepgram for session {session_id}")
                    return True
                    
                logger.debug(f"Waiting for Deepgram connection: attempt {i+1}/50")
                await asyncio.sleep(0.1)

            logger.error(f"Timeout waiting for Deepgram connection for session {session_id}")
            return False
        except Exception as e:
            logger.error(f"Error initializing Deepgram session for {session_id}: {str(e)}")
            return False
        
        
    async def _session_handler(self, session_id: str, url: str, headers: Dict):
        retries = 3
        attempt = 0

        while attempt < retries:
            try:
                async with websockets.connect(url, extra_headers=headers) as websocket:
                    self.sessions[session_id]["websocket"] = websocket
                    self.sessions[session_id]["connected"] = True

                    async for message in websocket:
                        await self._handle_message(session_id, message)

            except Exception as e:
                attempt += 1
                self.sessions[session_id]["connected"] = False
                logger.warning(f"Deepgram session {session_id} disconnected: {e}. Retrying {attempt}/{retries}...")
                await asyncio.sleep(2 ** attempt)

        logger.error(f"Could not reconnect to Deepgram for session {session_id}")

        
    async def convert_twilio_audio(self, payload: str, session_id: str) -> bytes:
        """
        Convert Twilio's base64 audio format to raw PCM audio for Deepgram.
        
        Twilio sends audio as base64-encoded mulaw (G.711) audio at 8kHz.
        Deepgram expects linear PCM at 16kHz.
        
        Args:
            payload: Base64-encoded mulaw audio from Twilio
            session_id: Session identifier for logging
            
        Returns:
            Converted audio bytes ready for Deepgram
        """
        try:
            # Decode base64 encoded payload from Twilio
            mulaw_audio = base64.b64decode(payload)
            
            # Convert mulaw to PCM (linear16)
            pcm_audio = audioop.ulaw2lin(mulaw_audio, 2)  # 2 bytes per sample (16-bit)
            
            # Upsample from 8kHz to 16kHz
            pcm_audio_16k = audioop.ratecv(pcm_audio, 2, 1, 8000, 16000, None)[0]
            
            logger.debug(f"Converted {len(mulaw_audio)} bytes of mulaw to {len(pcm_audio_16k)} bytes of PCM")
            return pcm_audio_16k
            
        except Exception as e:
            logger.error(f"Error converting Twilio audio for {session_id}: {str(e)}")
            return b''  # Return empty bytes on error    

    async def _handle_message(self, session_id: str, message: str):
        try:
            data = json.loads(message)
            message_type = data.get("type")
            logger.info(f"Deepgram message received: {message_type}")

            if message_type == "Results":
                channel = data.get("channel", {})
                alternatives = channel.get("alternatives", [])
                
                if alternatives:
                    transcript = alternatives[0].get("transcript", "").strip()
                    is_final = data.get("is_final", False)
                    speech_final = data.get("speech_final", False)
                    
                    logger.info(f"Deepgram transcript: '{transcript}', is_final={is_final}, speech_final={speech_final}")
                    
                    # Only send final transcripts to the callback
                    # speech_final means the entire speech segment is complete
                    # is_final just means this chunk is complete but the person might still be speaking
                    if transcript and is_final:
                        if speech_final:
                            logger.info(f"Final transcript ({session_id}): '{transcript}'")
                            # This is truly the final transcript, user has finished speaking
                            await self.sessions[session_id]["callback"](session_id, transcript)
                        else:
                            # This is a segment that's final, but the user might still be speaking
                            # We should update our transcripts but not necessarily process yet
                            logger.info(f"Segment final transcript ({session_id}): '{transcript}'")
                            # Store in app state but don't trigger processing yet
                            await self.sessions[session_id]["callback"](session_id, transcript)
                    else:
                        logger.debug(f"Interim transcript ({session_id}): '{transcript}'")
                else:
                    logger.debug(f"No transcript alternatives received for {session_id}")

            elif message_type == "UtteranceEnd":
                logger.info(f"Utterance end detected for {session_id}")
                # Notify that an utterance has ended - this helps with silence detection
                await self.sessions[session_id]["callback"](session_id, "")

            elif message_type == "Error":
                error_message = data.get('message', 'Unknown error')
                logger.error(f"Deepgram Error for {session_id}: {error_message}")

            elif message_type == "Metadata":
                logger.info(f"Deepgram Metadata received for {session_id}: {json.dumps(data)}")

            else:
                logger.debug(f"Unhandled message type '{message_type}' for session {session_id}")
        except Exception as e:
            logger.error(f"Error handling Deepgram message: {str(e)}")
            logger.error(f"Raw message content: {message[:100]}...")
                
    
    async def process_audio_chunk(self, session_id: str, audio_data: bytes) -> bool:
        try:
            session = self.sessions.get(session_id)
            if not session:
                logger.warning(f"No session found for {session_id}")
                return False
                
            if not session["connected"] or not session["websocket"]:
                logger.warning(f"Session {session_id} is not connected")
                return False
                
            if getattr(self, '_audio_chunk_count', 0) % 100 == 0:
                audio_sample = ', '.join([str(b) for b in audio_data[:20]])
                logger.info(f"Audio sample first 20 bytes: [{audio_sample}]")
            self._audio_chunk_count = getattr(self, '_audio_chunk_count', 0) + 1
            
            # Log some information about the audio data occasionally
            if getattr(self, '_audio_chunk_count', 0) % 50 == 0:
                logger.debug(f"Sending audio chunk to Deepgram: {len(audio_data)} bytes")
            self._audio_chunk_count = getattr(self, '_audio_chunk_count', 0) + 1
                
            try:
                await session["websocket"].send(audio_data)
                return True
            except websockets.exceptions.ConnectionClosed:
                logger.error(f"Connection closed for session {session_id}")
                session["connected"] = False
                return False
            except Exception as e:
                logger.error(f"Error sending audio chunk for {session_id}: {str(e)}")
                return False
        except Exception as e:
            logger.error(f"Unexpected error processing audio chunk: {str(e)}")
            return False
    
    async def close_session(self, session_id: str):
        session = self.sessions.pop(session_id, None)
        if session:
            if session["websocket"]:
                await session["websocket"].close()
            if session["task"] and not session["task"].done():
                session["task"].cancel()
            logger.info(f"Deepgram session {session_id} closed")
            return True
        return False
