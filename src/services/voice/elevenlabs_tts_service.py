import base64
import asyncio
import logging
from typing import AsyncGenerator
from elevenlabs import ElevenLabs, VoiceSettings
from config.settings import settings

logger = logging.getLogger(__name__)

class ElevenLabsTTSService:
    """ElevenLabs Text-to-Speech Service"""
    
    def __init__(self):
        self.client = ElevenLabs(api_key=settings.elevenlabs_api_key)
        self.voice_id = settings.elevenlabs_voice_id or "21m00Tcm4TlvDq8ikWAM"
        logger.info("ElevenLabs TTS initialized")
    
    async def generate(self, text: str) -> AsyncGenerator[str, None]:
        """Generate audio from text - yields base64 encoded chunks"""
        if not text or not text.strip():
            logger.warning("Empty text for TTS")
            return
        
        logger.info(f"ElevenLabs TTS: '{text[:50]}...'")
        
        try:
            loop = asyncio.get_event_loop()
            
            audio_generator = self.client.text_to_speech.stream(
                text=text,
                voice_id=self.voice_id,
                model_id="eleven_turbo_v2_5",
                output_format="ulaw_8000",
                voice_settings=VoiceSettings(
                    stability=0.5,
                    similarity_boost=0.75,
                    style=0.0,
                    use_speaker_boost=True
                )
            )
            
            chunk_count = 0
            
            def get_next_chunk():
                try:
                    return next(audio_generator)
                except StopIteration:
                    return None
            
            while True:
                chunk = await loop.run_in_executor(None, get_next_chunk)
                if chunk is None:
                    break
                
                if chunk:
                    audio_b64 = base64.b64encode(chunk).decode('ascii')
                    chunk_count += 1
                    yield audio_b64
            
            logger.info(f"ElevenLabs complete: {chunk_count} chunks")
            
        except Exception as e:
            logger.error(f"ElevenLabs error: {e}")
            yield base64.b64encode(b"").decode('ascii')
