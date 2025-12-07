# src/services/speech/deepgram_ws_service.py - ORIGINAL (NO CHANGES)

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
        
    async def initialize_session(
        self, 
        session_id: str, 
        callback: Callable[[str, str], Awaitable[None]],
        interruption_callback: Optional[Callable[[str, str, float], Awaitable[None]]] = None
    ) -> bool:
        """
        Initialize Deepgram session with optional interruption detection.
        
        Args:
            session_id: Unique session identifier
            callback: Called with (session_id, transcript) for FINAL transcripts
            interruption_callback: Called with (session_id, transcript, confidence) for INTERIM transcripts
        """
        try:
            if session_id in self.sessions:
                logger.info(f"Closing existing session for {session_id}")
                await self.close_session(session_id)

            if not self.deepgram_api_key:
                logger.error(f"No Deepgram API key found")
                return False

            logger.info(f"Initializing Deepgram session {session_id}")
            
            # Initialize Deepgram client with keepalive
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
                "interruption_callback": interruption_callback,
                "last_interim_text": "",
                "interruption_cooldown": False,
            }
            self.sessions[session_id] = session
            
            # Event handlers with flexible signatures to handle SDK variations
            async def on_open(*args, **kwargs):
                logger.info(f"✅ Deepgram connected: {session_id}")
                session["connected"] = True
            
            async def on_message(*args, **kwargs):
                try:
                    # Handle both positional and keyword arguments
                    result = args[1] if len(args) > 1 else kwargs.get('result')
                    if result is None:
                        return
                    
                    sentence = result.channel.alternatives[0].transcript
                    
                    if len(sentence) == 0:
                        return
                    
                    # Get confidence score
                    confidence = getattr(result.channel.alternatives[0], 'confidence', 0.0)
                    
                    if result.is_final:
                        # FINAL transcript
                        logger.info(f"📝 Final: '{sentence}'")
                        session["last_interim_text"] = ""
                        session["interruption_cooldown"] = False
                        # Call directly - route handler handles non-blocking
                        try:
                            await callback(session_id, sentence)
                        except Exception as e:
                            logger.error(f"Callback error: {e}")
                    else:
                        # INTERIM transcript - for interruption detection
                        word_count = len(sentence.split())
                        
                        # Only trigger if different from last interim (avoid duplicates)
                        if sentence != session["last_interim_text"] and not session["interruption_cooldown"]:
                            session["last_interim_text"] = sentence
                            
                            # Trigger interruption callback
                            if interruption_callback and word_count >= 2:
                                confidence_threshold = 0.3
                                
                                if confidence == 0.0 or confidence >= confidence_threshold:
                                    logger.info(f"🎯 TRIGGERING INTERRUPTION: '{sentence}' ({word_count} words, conf: {confidence:.2f})")
                                    
                                    # Set cooldown to prevent rapid-fire
                                    session["interruption_cooldown"] = True
                                    
                                    # Fire interruption - must be fast!
                                    try:
                                        await interruption_callback(session_id, sentence, confidence)
                                    except Exception as e:
                                        logger.error(f"Interruption callback error: {e}")
                                    
                                    # Reset cooldown after short delay
                                    asyncio.create_task(self._reset_cooldown(session_id))
                        
                except Exception as e:
                    logger.error(f"Error in on_message: {str(e)}")
                    import traceback
                    logger.error(traceback.format_exc())
            
            async def on_metadata(*args, **kwargs):
                logger.debug(f"Metadata received")
            
            async def on_speech_started(*args, **kwargs):
                logger.debug(f"🎤 Speech started detected")
            
            async def on_utterance_end(*args, **kwargs):
                logger.debug(f"Utterance ended")
                # Reset cooldown on utterance end
                if session_id in self.sessions:
                    self.sessions[session_id]["interruption_cooldown"] = False
            
            async def on_close(*args, **kwargs):
                logger.info(f"Deepgram closed: {session_id}")
                if session_id in self.sessions:
                    self.sessions[session_id]["connected"] = False
            
            async def on_error(*args, **kwargs):
                error = args[1] if len(args) > 1 else kwargs.get('error', 'Unknown error')
                logger.error(f"Deepgram error: {error}")
            
            async def on_unhandled(*args, **kwargs):
                unhandled = args[1] if len(args) > 1 else kwargs.get('unhandled', '')
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
            
            # YOUR EXACT ORIGINAL OPTIONS - NO CHANGES
            options = LiveOptions(
                model="nova-2",
                language="en-US",
                encoding="linear16",
                sample_rate=16000,
                channels=1,
                punctuate=True,
                smart_format=True,
                interim_results=True,
                endpointing=250,       # YOUR ORIGINAL VALUE
                utterance_end_ms=1000, # YOUR ORIGINAL VALUE
                vad_events=True,
            )
            
            # Start connection
            if not await dg_connection.start(options):
                logger.error(f"Failed to start Deepgram connection")
                return False
            
            # Wait for connection with timeout
            for i in range(50):  # 5 seconds max
                if session["connected"]:
                    logger.info(f"✅ Deepgram ready: {session_id}")
                    return True
                await asyncio.sleep(0.1)
            
            logger.error(f"Timeout waiting for Deepgram connection")
            return False
            
        except Exception as e:
            logger.error(f"Error initializing Deepgram: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    async def _reset_cooldown(self, session_id: str):
        """Reset interruption cooldown after delay"""
        await asyncio.sleep(0.5)
        if session_id in self.sessions:
            self.sessions[session_id]["interruption_cooldown"] = False
            self.sessions[session_id]["last_interim_text"] = ""
    
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
        """Send audio chunk to Deepgram for processing"""
        try:
            session = self.sessions.get(session_id)
            if not session or not session["connected"]:
                return False
            
            connection = session["connection"]
            await connection.send(audio_data)
            return True
            
        except Exception as e:
            logger.error(f"Error sending audio: {str(e)}")
            return False
    
    async def close_session(self, session_id: str):
        """Close a Deepgram session"""
        session = self.sessions.pop(session_id, None)
        if session and session.get("connection"):
            try:
                await session["connection"].finish()
                logger.info(f"✅ Deepgram session closed: {session_id}")
            except Exception as e:
                logger.error(f"Error closing session: {str(e)}")
        return True
    
    def is_session_active(self, session_id: str) -> bool:
        """Check if a session is active"""
        session = self.sessions.get(session_id)
        return session is not None and session.get("connected", False)
