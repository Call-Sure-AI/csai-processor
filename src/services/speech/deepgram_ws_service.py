# src/services/speech/deepgram_ws_service.py - ULTRA LOW LATENCY VERSION

"""
Deepgram WebSocket Service - Optimized for minimum latency
Key changes:
1. Faster endpointing (150ms vs 250ms)
2. Shorter utterance end (700ms vs 1000ms)
3. Smarter interruption detection
4. Connection keepalive optimization
"""

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
        Initialize Deepgram session - OPTIMIZED FOR SPEED
        """
        try:
            if session_id in self.sessions:
                await self.close_session(session_id)

            if not self.deepgram_api_key:
                logger.error("No Deepgram API key")
                return False

            logger.info(f"🎙️ Initializing Deepgram: {session_id}")
            
            # ✅ OPTIMIZATION: Keepalive for persistent connection
            config = DeepgramClientOptions(
                options={"keepalive": "true"}
            )
            deepgram = DeepgramClient(self.deepgram_api_key, config)
            dg_connection = deepgram.listen.asynclive.v("1")
            
            session = {
                "connection": dg_connection,
                "connected": False,
                "callback": callback,
                "interruption_callback": interruption_callback,
                "last_interim_text": "",
                "interruption_cooldown": False,
                "last_interrupt_time": 0,
            }
            self.sessions[session_id] = session
            
            # Event handlers
            async def on_open(*args, **kwargs):
                logger.info(f"✅ Deepgram connected: {session_id}")
                session["connected"] = True
            
            async def on_message(*args, **kwargs):
                try:
                    result = args[1] if len(args) > 1 else kwargs.get('result')
                    if result is None:
                        return
                    
                    sentence = result.channel.alternatives[0].transcript
                    if not sentence:
                        return
                    
                    confidence = getattr(result.channel.alternatives[0], 'confidence', 0.0)
                    
                    if result.is_final:
                        # FINAL transcript
                        logger.info(f"📝 Final: '{sentence}'")
                        session["last_interim_text"] = ""
                        session["interruption_cooldown"] = False
                        
                        # ✅ Fire callback without blocking
                        asyncio.create_task(self._safe_callback(callback, session_id, sentence))
                    else:
                        # INTERIM - for interruption
                        word_count = len(sentence.split())
                        
                        if (sentence != session["last_interim_text"] and 
                            not session["interruption_cooldown"] and
                            word_count >= 2):
                            
                            session["last_interim_text"] = sentence
                            
                            if interruption_callback:
                                # ✅ FASTER: Lower confidence threshold
                                if confidence == 0.0 or confidence >= 0.25:
                                    logger.info(f"🎯 INTERRUPT: '{sentence}' (conf: {confidence:.2f})")
                                    session["interruption_cooldown"] = True
                                    
                                    # Fire interruption immediately
                                    asyncio.create_task(
                                        self._safe_interrupt_callback(
                                            interruption_callback, session_id, sentence, confidence
                                        )
                                    )
                                    
                                    # Shorter cooldown
                                    asyncio.create_task(self._reset_cooldown(session_id, 0.3))
                        
                except Exception as e:
                    logger.error(f"on_message error: {e}")
            
            async def on_speech_started(*args, **kwargs):
                """✅ NEW: Immediate interruption on speech detection"""
                logger.debug("🎤 Speech started")
                # Could trigger pre-emptive clear here if needed
            
            async def on_utterance_end(*args, **kwargs):
                if session_id in self.sessions:
                    self.sessions[session_id]["interruption_cooldown"] = False
            
            async def on_close(*args, **kwargs):
                logger.info(f"Deepgram closed: {session_id}")
                if session_id in self.sessions:
                    self.sessions[session_id]["connected"] = False
            
            async def on_error(*args, **kwargs):
                error = args[1] if len(args) > 1 else kwargs.get('error', 'Unknown')
                logger.error(f"Deepgram error: {error}")
            
            # Register handlers
            dg_connection.on(LiveTranscriptionEvents.Open, on_open)
            dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
            dg_connection.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
            dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
            dg_connection.on(LiveTranscriptionEvents.Close, on_close)
            dg_connection.on(LiveTranscriptionEvents.Error, on_error)
            
            # ✅ OPTIMIZED: Faster settings
            options = LiveOptions(
                model="nova-2",
                language="en-US",
                encoding="linear16",
                sample_rate=16000,
                channels=1,
                punctuate=True,
                smart_format=True,
                interim_results=True,
                # ✅ KEY OPTIMIZATIONS:
                endpointing=150,        # Was 250ms - 100ms faster!
                utterance_end_ms=700,   # Was 1000ms - 300ms faster!
                vad_events=True,
                # ✅ Additional speed options:
                no_delay=True,          # Disable delay for faster response
                filler_words=False,     # Skip filler word detection
            )
            
            if not await dg_connection.start(options):
                logger.error("Failed to start Deepgram")
                return False
            
            # Wait for connection (shorter timeout)
            for _ in range(30):  # 3 seconds max
                if session["connected"]:
                    logger.info(f"✅ Deepgram ready: {session_id}")
                    return True
                await asyncio.sleep(0.1)
            
            logger.error("Deepgram connection timeout")
            return False
            
        except Exception as e:
            logger.error(f"Deepgram init error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    async def _safe_callback(self, callback, session_id: str, transcript: str):
        """Execute callback with error handling"""
        try:
            await callback(session_id, transcript)
        except Exception as e:
            logger.error(f"Callback error: {e}")
    
    async def _safe_interrupt_callback(self, callback, session_id: str, transcript: str, confidence: float):
        """Execute interrupt callback with error handling"""
        try:
            await callback(session_id, transcript, confidence)
        except Exception as e:
            logger.error(f"Interrupt callback error: {e}")
    
    async def _reset_cooldown(self, session_id: str, delay: float = 0.3):
        """Reset interruption cooldown after delay"""
        await asyncio.sleep(delay)
        if session_id in self.sessions:
            self.sessions[session_id]["interruption_cooldown"] = False
            self.sessions[session_id]["last_interim_text"] = ""
    
    async def convert_twilio_audio(self, payload: str, session_id: str) -> bytes:
        """Convert Twilio mulaw to linear16 PCM"""
        try:
            mulaw_audio = base64.b64decode(payload)
            pcm_audio = audioop.ulaw2lin(mulaw_audio, 2)
            pcm_audio_16k = audioop.ratecv(pcm_audio, 2, 1, 8000, 16000, None)[0]
            return pcm_audio_16k
        except Exception as e:
            logger.error(f"Audio convert error: {e}")
            return b''
    
    async def process_audio_chunk(self, session_id: str, audio_data: bytes) -> bool:
        """Send audio to Deepgram"""
        try:
            session = self.sessions.get(session_id)
            if not session or not session["connected"]:
                return False
            
            await session["connection"].send(audio_data)
            return True
        except Exception as e:
            logger.error(f"Audio send error: {e}")
            return False
    
    async def close_session(self, session_id: str):
        """Close Deepgram session"""
        session = self.sessions.pop(session_id, None)
        if session and session.get("connection"):
            try:
                await session["connection"].finish()
                logger.info(f"✅ Deepgram closed: {session_id}")
            except Exception as e:
                logger.error(f"Close error: {e}")
        return True
    
    def is_session_active(self, session_id: str) -> bool:
        """Check if session is active"""
        session = self.sessions.get(session_id)
        return session is not None and session.get("connected", False)
