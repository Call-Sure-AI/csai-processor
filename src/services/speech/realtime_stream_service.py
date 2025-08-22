import asyncio
from typing import Optional, List, Tuple
from dataclasses import dataclass
import numpy as np
from collections import deque
import time

@dataclass
class SpeechSegment:
    text: str
    confidence: float
    is_final: bool
    timestamp: float

class RealTimeSTTHandler:
    """Handles real-time speech-to-text with VAD and interruption detection"""
    
    def __init__(self, provider: str = "deepgram"):
        self.provider = provider
        self.vad_handler = VoiceActivityDetector()
        self.partial_transcript = ""
        self.final_transcript = ""
        self.speech_segments = deque(maxlen=10)
        self.last_speech_time = 0
        self.min_speech_confidence = 0.7
        
    async def initialize(self):
        """Initialize STT provider connection"""
        if self.provider == "deepgram":
            from deepgram import Deepgram
            self.client = Deepgram(settings.deepgram_api_key)
            self.live_options = {
                "model": "nova-2-phonecall",
                "language": "en-US",
                "encoding": "linear16",
                "sample_rate": 8000,
                "channels": 1,
                "interim_results": True,
                "endpointing": 300,
                "vad_events": True,
                "smart_format": True
            }
        elif self.provider == "assemblyai":
            # Initialize AssemblyAI
            pass
            
    async def process_audio(self, audio_chunk: bytes) -> Optional[str]:
        """Process audio chunk and return transcribed text if available"""
        # Check for voice activity
        has_speech = self.vad_handler.process_chunk(audio_chunk)
        
        if not has_speech and time.time() - self.last_speech_time > 1.5:
            # User stopped speaking - return final transcript
            if self.final_transcript:
                result = self.final_transcript
                self.final_transcript = ""
                self.partial_transcript = ""
                return result
                
        if has_speech:
            self.last_speech_time = time.time()
            
            # Send to STT provider
            transcript = await self._transcribe_chunk(audio_chunk)
            
            if transcript:
                if transcript.is_final:
                    self.final_transcript += " " + transcript.text
                    self.partial_transcript = ""
                else:
                    self.partial_transcript = transcript.text
                    
                self.speech_segments.append(transcript)
                
        return None
        
    async def _transcribe_chunk(self, audio: bytes) -> Optional[SpeechSegment]:
        """Send audio to STT provider and get transcript"""
        # Provider-specific implementation
        pass
        
    def get_current_transcript(self) -> str:
        """Get the current working transcript"""
        return self.final_transcript + " " + self.partial_transcript

class VoiceActivityDetector:
    """Simple VAD using energy-based detection"""
    
    def __init__(self, threshold: float = 0.01, frame_duration_ms: int = 20):
        self.threshold = threshold
        self.frame_duration_ms = frame_duration_ms
        self.speech_frames = deque(maxlen=10)
        
    def process_chunk(self, audio_chunk: bytes) -> bool:
        """Detect if chunk contains speech"""
        # Convert bytes to numpy array
        audio_array = np.frombuffer(audio_chunk, dtype=np.int16)
        
        # Calculate RMS energy
        rms = np.sqrt(np.mean(audio_array.astype(float) ** 2))
        normalized_rms = rms / 32768.0  # Normalize for 16-bit audio
        
        # Simple threshold-based detection
        has_speech = normalized_rms > self.threshold
        self.speech_frames.append(has_speech)
        
        # Require multiple frames with speech for stability
        return sum(self.speech_frames) >= 3

class RealTimeTTSHandler:
    """Handles text-to-speech synthesis with streaming support"""
    
    def __init__(self, provider: str = "elevenlabs"):
        self.provider = provider
        self.voice_id = None
        self.prosody_settings = {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "speaking_rate": 1.0
        }
        
    async def initialize(self, voice_id: Optional[str] = None):
        """Initialize TTS provider"""
        if self.provider == "elevenlabs":
            from elevenlabs import ElevenLabs
            self.client = ElevenLabs(api_key=settings.elevenlabs_api_key)
            self.voice_id = voice_id or "default_voice_id"
        elif self.provider == "azure":
            # Initialize Azure Cognitive Services
            pass
            
    async def synthesize(self, text: str, stream: bool = True) -> bytes:
        """Convert text to speech audio"""
        if self.provider == "elevenlabs":
            return await self._synthesize_elevenlabs(text, stream)
        elif self.provider == "azure":
            return await self._synthesize_azure(text, stream)
            
    async def _synthesize_elevenlabs(self, text: str, stream: bool) -> bytes:
        """Synthesize using ElevenLabs"""
        # Implementation for ElevenLabs streaming
        audio_stream = self.client.generate(
            text=text,
            voice=self.voice_id,
            model="eleven_turbo_v2",
            stream=stream,
            **self.prosody_settings
        )
        
        if stream:
            audio_chunks = []
            async for chunk in audio_stream:
                audio_chunks.append(chunk)
            return b''.join(audio_chunks)
        else:
            return audio_stream
        
real_time_stt_handler = RealTimeSTTHandler()