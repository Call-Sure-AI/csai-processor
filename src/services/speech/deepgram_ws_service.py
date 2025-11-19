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
                logger.error(f"No Deepgram API key found for session {session_id}")
                return False

            logger.info(f"Initializing Deepgram session {session_id}")
            
            # Initialize Deepgram client
            config = DeepgramClientOptions(options={"keepalive": "true"})
            deepgram = DeepgramClient(self.deepgram_api_key, config)
            
            # Create connection
            dg_connection = deepgram.listen.asyncwebsocket.v("1")
            
            session = {
                "connection": dg_connection,
                "connected": False,
                "callback": callback,
            }
            self.sessions[session_id] = session
            
            # Set up event handlers
            async def on_open(self_inner, open_event, **kwargs):
                logger.info(f"Deepgram connected for {session_id}")
                session["connected"] = True
            
            async def on_message(self_inner, result, **kwargs):
                try:
                    sentence = result.channel.alternatives[0].transcript
                    
                    if len(sentence) == 0:
                        return
                    
                    if result.is_final:
                        logger.info(f"Deepgram transcript (final): '{sentence}'")
                        await callback(session_id, sentence)
                    else:
                        logger.debug(f"Interim: '{sentence}'")
                        
                except Exception as e:
                    logger.error(f"Error in on_message: {str(e)}")
            
            async def on_metadata(self_inner, metadata, **kwargs):
                logger.debug(f"Deepgram metadata: {metadata}")
            
            async def on_speech_started(self_inner, speech_started, **kwargs):
                logger.debug(f"Speech started")
            
            async def on_utterance_end(self_inner, utterance_end, **kwargs):
                logger.debug(f"Utterance ended")
            
            async def on_close(self_inner, close_event, **kwargs):
                logger.info(f"Deepgram connection closed for {session_id}")
                session["connected"] = False
            
            async def on_error(self_inner, error, **kwargs):
                logger.error(f"Deepgram error for {session_id}: {error}")
            
            async def on_unhandled(self_inner, unhandled, **kwargs):
                logger.debug(f"Deepgram unhandled event: {unhandled}")
            
            # Register event handlers
            dg_connection.on(LiveTranscriptionEvents.Open, on_open)
            dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
            dg_connection.on(LiveTranscriptionEvents.Metadata, on_metadata)
            dg_connection.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
            dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
            dg_connection.on(LiveTranscriptionEvents.Close, on_close)
            dg_connection.on(LiveTranscriptionEvents.Error, on_error)
            dg_connection.on(LiveTranscriptionEvents.Unhandled, on_unhandled)
            
            # Configure options
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
            await dg_connection.start(options)
            
            # Wait for connection
            for i in range(50):
                if session["connected"]:
                    logger.info(f"Deepgram session ready: {session_id}")
                    return True
                await asyncio.sleep(0.1)
            
            logger.error(f"Timeout connecting to Deepgram for {session_id}")
            return False
            
        except Exception as e:
            logger.error(f"Error initializing Deepgram session for {session_id}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    async def convert_twilio_audio(self, payload: str, session_id: str) -> bytes:
        """Convert Twilio's mulaw audio to PCM for Deepgram"""
        try:
            mulaw_audio = base64.b64decode(payload)
            pcm_audio = audioop.ulaw2lin(mulaw_audio, 2)
            pcm_audio_16k = audioop.ratecv(pcm_audio, 2, 1, 8000, 16000, None)[0]
            return pcm_audio_16k
        except Exception as e:
            logger.error(f"Error converting audio for {session_id}: {str(e)}")
            return b''
    
    async def process_audio_chunk(self, session_id: str, audio_data: bytes) -> bool:
        try:
            session = self.sessions.get(session_id)
            if not session or not session["connected"]:
                return False
            
            connection = session["connection"]
            await connection.send(audio_data)
            return True
            
        except Exception as e:
            logger.error(f"Error processing audio chunk: {str(e)}")
            return False
    
    async def close_session(self, session_id: str):
        session = self.sessions.pop(session_id, None)
        if session and session.get("connection"):
            try:
                await session["connection"].finish()
                logger.info(f"Deepgram session {session_id} closed")
            except:
                pass
        return True
