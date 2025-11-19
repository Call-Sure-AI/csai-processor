import asyncio
import json
import logging
import os
from typing import Dict, Callable, Awaitable, Optional
import base64
import audioop
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)

logger = logging.getLogger(__name__)

class DeepgramWebSocketService:
    def __init__(self):
        self.sessions: Dict[str, Dict] = {}
        self.deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
        
    async def initialize_session(self, session_id: str, callback: Callable[[str, str], Awaitable[None]]) -> bool:
        try:
            if session_id in self.sessions:
                logger.info(f"Closing existing session for {session_id}")
                await self.close_session(session_id)

            if not self.deepgram_api_key:
                logger.error(f"No Deepgram API key found")
                return False

            logger.info(f"Initializing Deepgram session {session_id}")
            
            # Initialize Deepgram client with SDK 3.7.2
            config = DeepgramClientOptions(
                options={"keepalive": "true"}
            )
            deepgram = DeepgramClient(self.deepgram_api_key, config)
            
            # Create live transcription connection
            dg_connection = deepgram.listen.asynclive.v("1")
            
            session = {
                "connection": dg_connection,
                "connected": False,
                "callback": callback,
            }
            self.sessions[session_id] = session
            
            # Event handlers
            async def on_open(self_inner, open_event, **kwargs):
                logger.info(f"Deepgram connected: {session_id}")
                session["connected"] = True
            
            async def on_message(self_inner, result, **kwargs):
                try:
                    sentence = result.channel.alternatives[0].transcript
                    
                    if len(sentence) == 0:
                        return
                    
                    if result.is_final:
                        logger.info(f"Deepgram final: '{sentence}'")
                        await callback(session_id, sentence)
                    else:
                        logger.debug(f"Interim: '{sentence}'")
                        
                except Exception as e:
                    logger.error(f"Error in on_message: {str(e)}")
            
            async def on_metadata(self_inner, metadata, **kwargs):
                logger.debug(f"Metadata received")
            
            async def on_speech_started(self_inner, speech_started, **kwargs):
                logger.debug(f"Speech started")
            
            async def on_utterance_end(self_inner, utterance_end, **kwargs):
                logger.debug(f"Utterance ended")
            
            async def on_close(self_inner, close_event, **kwargs):
                logger.info(f"Deepgram closed: {session_id}")
                session["connected"] = False
            
            async def on_error(self_inner, error, **kwargs):
                logger.error(f"Deepgram error: {error}")
            
            async def on_unhandled(self_inner, unhandled, **kwargs):
                logger.debug(f"Unhandled event: {unhandled}")
            
            # Register all event handlers
            dg_connection.on(LiveTranscriptionEvents.Open, on_open)
            dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
            dg_connection.on(LiveTranscriptionEvents.Metadata, on_metadata)
            dg_connection.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
            dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
            dg_connection.on(LiveTranscriptionEvents.Close, on_close)
            dg_connection.on(LiveTranscriptionEvents.Error, on_error)
            dg_connection.on(LiveTranscriptionEvents.Unhandled, on_unhandled)
            
            # Configure live transcription options
            options = LiveOptions(
                model="nova-2",
                language="en-US",
                encoding="linear16",
                sample_rate=16000,
                channels=1,
                punctuate=True,
                smart_format=True,
                interim_results=False,
                endpointing=300,
            )
            
            # Start connection
            if not await dg_connection.start(options):
                logger.error(f"Failed to start Deepgram connection")
                return False
            
            # Wait for connection to be established
            for i in range(50):  # 5 seconds max
                if session["connected"]:
                    logger.info(f"Deepgram ready: {session_id}")
                    return True
                await asyncio.sleep(0.1)
            
            logger.error(f"Timeout waiting for Deepgram connection")
            return False
            
        except Exception as e:
            logger.error(f"Error initializing Deepgram: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    async def convert_twilio_audio(self, payload: str, session_id: str) -> bytes:
        """
        Convert Twilio's mulaw audio (8kHz) to linear16 PCM (16kHz) for Deepgram
        """
        try:
            # Decode base64 mulaw audio from Twilio
            mulaw_audio = base64.b64decode(payload)
            
            # Convert mulaw to linear PCM (16-bit)
            pcm_audio = audioop.ulaw2lin(mulaw_audio, 2)
            
            # Resample from 8kHz to 16kHz
            pcm_audio_16k = audioop.ratecv(pcm_audio, 2, 1, 8000, 16000, None)[0]
            
            return pcm_audio_16k
            
        except Exception as e:
            logger.error(f"Error converting audio: {str(e)}")
            return b''
    
    async def process_audio_chunk(self, session_id: str, audio_data: bytes) -> bool:
        try:
            session = self.sessions.get(session_id)
            if not session or not session["connected"]:
                return False
            
            connection = session["connection"]
            connection.send(audio_data)
            return True
            
        except Exception as e:
            logger.error(f"Error sending audio: {str(e)}")
            return False
    
    async def close_session(self, session_id: str):
        session = self.sessions.pop(session_id, None)
        if session and session.get("connection"):
            try:
                await session["connection"].finish()
                logger.info(f"✅ Deepgram session closed: {session_id}")
            except Exception as e:
                logger.error(f"Error closing session: {str(e)}")
        return True
