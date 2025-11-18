import asyncio
import base64
import logging
from typing import Callable, Awaitable
from deepgram import DeepgramClient, DeepgramClientOptions, LiveTranscriptionEvents, LiveOptions
from config.settings import settings

logger = logging.getLogger(__name__)

TranscriptCallback = Callable[[str, float], Awaitable[None]]

class DeepgramSTTService:
    """Deepgram Speech-to-Text Service optimized for Indian English"""
    
    def __init__(self, on_speech_end_callback: TranscriptCallback):
        self.dg_connection = None
        self.final_result = ""
        self._on_speech_end = on_speech_end_callback
        self.audio_sent_count = 0
        self._connection_established = False
        
        logger.info("=" * 80)
        logger.info("DeepgramSTT -> Initializing SDK 3.7.2")
        logger.info("=" * 80)
        self._initialize_connection()
    
    def _initialize_connection(self):
        api_key = settings.deepgram_api_key
        if not api_key:
            logger.error("DEEPGRAM_API_KEY not set")
            return
        
        try:
            config = DeepgramClientOptions(api_key=api_key)
            self.client = DeepgramClient("", config)
            self.LiveTranscriptionEvents = LiveTranscriptionEvents
            self.LiveOptions = LiveOptions
            self.initialized = True
            logger.info("✓ Deepgram initialized")
        except Exception as e:
            logger.error(f"Deepgram init failed: {e}")
            self.initialized = False
    
    async def connect(self) -> bool:
        if not self.initialized:
            return False
        
        try:
            logger.info("Connecting to Deepgram...")
            self.dg_connection = self.client.listen.asynclive.v("1")
            
            # Register async handlers
            self.dg_connection.on(self.LiveTranscriptionEvents.Open, self._on_open)
            self.dg_connection.on(self.LiveTranscriptionEvents.Transcript, self._on_transcript)
            self.dg_connection.on(self.LiveTranscriptionEvents.Error, self._on_error)
            self.dg_connection.on(self.LiveTranscriptionEvents.Close, self._on_close)
            self.dg_connection.on(self.LiveTranscriptionEvents.UtteranceEnd, self._on_utterance_end)
            
            # Optimized config for Indian English phone calls
            options = self.LiveOptions(
                model="nova-2-phonecall",
                language="en-IN",
                encoding="mulaw",
                sample_rate=8000,
                channels=1,
                smart_format=True,
                punctuate=True,
                filler_words=False,
                interim_results=True,
                endpointing=400,
                utterance_end_ms=1500,
                numerals=True
            )
            
            await self.dg_connection.start(options)
            logger.info("✓ Deepgram connected!")
            return True
            
        except Exception as e:
            logger.error(f"Deepgram connection failed: {e}")
            return False
    
    async def _on_open(self, *args, **kwargs):
        self._connection_established = True
        logger.info("Deepgram WebSocket opened")
    
    async def _on_utterance_end(self, *args, **kwargs):
        if self.final_result.strip():
            final_text = self.final_result.strip()
            logger.info(f"Utterance END: '{final_text}'")
            await self._on_speech_end(final_text, 0)
            self.final_result = ""
    
    async def _on_transcript(self, *args, **kwargs):
        try:
            result = kwargs.get('result') or (args[0] if args else None)
            if not result:
                return
            
            text = ''
            if hasattr(result, 'channel'):
                channel = result.channel
                if hasattr(channel, 'alternatives') and channel.alternatives:
                    text = channel.alternatives[0].transcript or ''
            
            if not text.strip():
                return
            
            is_final = getattr(result, 'is_final', False)
            
            if is_final:
                self.final_result += f" {text}"
                logger.info(f"FINAL: '{text}'")
                
                speech_final = getattr(result, 'speech_final', False)
                if speech_final:
                    final_text = self.final_result.strip()
                    logger.info(f"SPEECH FINAL: '{final_text}'")
                    await self._on_speech_end(final_text, 0)
                    self.final_result = ""
            else:
                logger.debug(f"Interim: '{text}'")
                
        except Exception as e:
            logger.error(f"Transcript error: {e}")
    
    async def _on_error(self, *args, **kwargs):
        error = kwargs.get('error') or (args[0] if args else 'Unknown')
        logger.error(f"Deepgram error: {error}")
    
    async def _on_close(self, *args, **kwargs):
        logger.info(f"Deepgram closed ({self.audio_sent_count} chunks)")
        self._connection_established = False
    
    async def send_audio(self, audio_chunk: bytes):
        if self.dg_connection:
            try:
                await self.dg_connection.send(audio_chunk)
                self.audio_sent_count += 1
            except Exception as e:
                logger.error(f"Send audio error: {e}")
    
    def send(self, payload: str):
        """Send base64 encoded audio"""
        if not self.dg_connection:
            return
        
        try:
            audio_bytes = base64.b64decode(payload)
            asyncio.create_task(self.send_audio(audio_bytes))
        except Exception as e:
            logger.error(f"Decode error: {e}")
    
    async def close(self):
        if self.dg_connection:
            try:
                await self.dg_connection.finish()
                self.dg_connection = None
            except Exception as e:
                logger.error(f"Close error: {e}")
