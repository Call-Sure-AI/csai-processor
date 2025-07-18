# services/speech/stt_service.py

import logging
import asyncio
from typing import Dict, Optional, Callable, Awaitable, Any
import io
import wave
import base64
import aiohttp
import os
import json
import time


logger = logging.getLogger(__name__)
class SpeechToTextService:
    """Service to handle speech-to-text conversion for Twilio calls using Deepgram"""
    
    def __init__(self):
        self.active_sessions: Dict[str, Dict] = {}
        
        # Get Deepgram API key from environment variable or use a default for development
        self.deepgram_api_key = os.environ.get("DEEPGRAM_API_KEY")
        self.deepgram_url = "https://api.deepgram.com/v1/listen"
        if not self.deepgram_api_key:
            logger.warning("DEEPGRAM_API_KEY environment variable not set - speech recognition will fail")
            
    
    async def process_audio_chunk(self, session_id: str, audio_data: bytes, 
                                callback: Optional[Callable[[str, str], Awaitable[Any]]] = None):
        """Process audio chunks with intelligent buffering and transcription"""
        try:
            # Initialize session if not exists
            if session_id not in self.active_sessions:
                self.active_sessions[session_id] = {
                    "buffer": bytearray(),
                    "last_activity": time.time(),
                    "chunk_count": 0,
                    "energy_levels": []
                }
            
            session = self.active_sessions[session_id]
            session["buffer"].extend(audio_data)
            session["chunk_count"] += 1
            session["last_activity"] = time.time()
            
            # Analyze audio energy
            silence_level = 128
            active_bytes = sum(1 for b in audio_data if abs(b - silence_level) > 10)
            energy_percentage = (active_bytes / len(audio_data)) * 100
            
            # Track energy levels
            session["energy_levels"].append(energy_percentage)
            if len(session["energy_levels"]) > 10:
                session["energy_levels"].pop(0)
            
            # Determine if buffer should be processed
            buffer_length = len(session["buffer"])
            avg_energy = sum(session["energy_levels"]) / len(session["energy_levels"])
            
            # Process conditions:
            # 1. Substantial buffer with consistent energy
            # 2. Large buffer accumulation
            # 3. Forced processing after timeout
            should_process = (
                buffer_length > 2000 and avg_energy > 10.0 or
                buffer_length > 10000 or
                (time.time() - session.get("last_process_time", 0) > 3.0 and buffer_length > 1000)
            )
            
            if should_process:
                # Prevent simultaneous processing
                session["last_process_time"] = time.time()
                
                # Convert buffer to bytes and clear
                buffer_bytes = bytes(session["buffer"])
                session["buffer"].clear()
                
                # Transcribe
                text = await self._recognize_speech(buffer_bytes, session_id)
                
                # Callback if text found
                if text and callback:
                    logger.info(f"Transcribed for {session_id}: '{text}'")
                    await callback(session_id, text)
            
            return True
        
        except Exception as e:
            logger.error(f"Error processing audio chunk for {session_id}: {str(e)}")
            return False
       
    async def _recognize_speech(self, audio_data: bytes, session_id: str) -> Optional[str]:
        """Enhanced speech recognition with detailed logging"""
        try:
            # Enhanced Deepgram API configuration
            headers = {
                "Authorization": f"Token {self.deepgram_api_key}",
                "Content-Type": "audio/x-mulaw",
            }
            
            url = (
                f"{self.deepgram_url}?"
                "model=nova-3&"
                "sample_rate=8000&"
                "encoding=mulaw&"
                "channels=1&"
                "punctuate=true&"
                "smart_format=true&"
                "filler_words=false&"
                "endpointing=true"
            )
            
            # Log audio characteristics
            logger.info(f"Transcription attempt for {session_id}: {len(audio_data)} bytes")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=headers,
                    data=audio_data,
                    timeout=10
                ) as response:
                    # Comprehensive error handling
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Deepgram API error {response.status}: {error_text}")
                        return None
                    
                    result = await response.json()
                    
                    # Detailed logging of Deepgram response
                    logger.info(f"Deepgram response for {session_id}: {json.dumps(result, indent=2)}")
                    
                    # Robust transcript extraction
                    try:
                        channel = result.get("results", {}).get("channels", [{}])[0]
                        alternatives = channel.get("alternatives", [])
                        
                        if alternatives:
                            transcript = alternatives[0].get("transcript", "").strip()
                            confidence = alternatives[0].get("confidence", 0.0)
                            
                            if transcript and confidence > 0.5:
                                logger.info(f"Valid transcript for {session_id}: '{transcript}' (confidence: {confidence})")
                                return transcript
                    except Exception as extract_err:
                        logger.error(f"Transcript extraction error: {extract_err}")
                    
                    logger.warning(f"No valid speech detected for {session_id}")
                    return None
        
        except Exception as e:
            logger.error(f"Speech recognition error for {session_id}: {str(e)}")
            return None
           
    def close_session(self, session_id: str):
        """Close a speech recognition session"""
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]
            logger.info(f"Closed speech recognition session {session_id}")
            return True
            
        return False
        
    async def process_final_buffer(self, session_id: str, 
                                callback: Optional[Callable[[str, str], Awaitable[Any]]] = None):
        """Process any remaining audio in the buffer when a session ends"""
        if session_id not in self.active_sessions:
            return False
            
        try:
            # Only process if we have enough data and not already processing
            buffer_size = len(self.active_sessions[session_id]["buffer"])
            
            # Reduce threshold from 8000 to 4000 bytes for faster processing
            if buffer_size > 4000 and not self.active_sessions[session_id]["processing"]:
                self.active_sessions[session_id]["processing"] = True
                
                # Get buffer copy
                audio_buffer = bytes(self.active_sessions[session_id]["buffer"])
                
                # Clear the buffer
                self.active_sessions[session_id]["buffer"] = bytearray()
                
                # Process audio through Deepgram
                text = await self._recognize_speech(audio_buffer, session_id)
                
                # If text was recognized and callback provided
                if text and callback and text.strip():
                    logger.info(f"Final buffer recognized: '{text}' for session {session_id}")
                    await callback(session_id, text)
                    
                self.active_sessions[session_id]["processing"] = False
                
            return True
            
        except Exception as e:
            logger.error(f"Error processing final buffer for session {session_id}: {str(e)}")
            return False   
        
            
    async def convert_twilio_audio(self, base64_payload: str, session_id: str) -> Optional[bytes]:
        """Convert Twilio's base64 audio format to raw bytes with robust audio analysis"""
        try:
            # Decode base64 audio
            audio_data = base64.b64decode(base64_payload)
            
            # Detailed energy calculation for μ-law audio
            silence_level = 128  # μ-law silence reference
            non_silent_bytes = [abs(b - silence_level) for b in audio_data]
            
            # More nuanced silence detection
            threshold = 10  # Adjust this value to fine-tune silence detection
            active_bytes = [b for b in non_silent_bytes if b > threshold]
            
            # Calculate energy metrics
            total_bytes = len(audio_data)
            active_count = len(active_bytes)
            silence_percentage = (total_bytes - active_count) / total_bytes * 100
            max_energy = max(non_silent_bytes) if non_silent_bytes else 0
            
            # Enhanced logging
            logger.info(
                f"Audio Conversion Details for {session_id}: "
                f"Base64 Input: {len(base64_payload)} chars, "
                f"Raw Bytes: {total_bytes}, "
                f"Active Bytes: {active_count}, "
                f"Max Energy: {max_energy}, "
                f"Silence: {silence_percentage:.2f}%"
            )
            
            # More sophisticated filtering
            # Only return audio with meaningful signal
            if active_count / total_bytes > 0.1:  # At least 10% active signal
                return audio_data
            
            return None
            
        except Exception as e:
            logger.error(f"Audio conversion error for {session_id}: {str(e)}")
            return None
    
    async def detect_silence(self, session_id: str, silence_threshold_sec: float = 0.8):
        """Check if there has been silence (no new audio) for a specified duration"""
        if session_id not in self.active_sessions:
            return False
            
        current_time = time.time()
        last_activity = self.active_sessions[session_id]["last_activity"]
        
        # Shorter silence threshold (was 2.0, now 0.8 seconds)
        return (current_time - last_activity) >= silence_threshold_sec
   
    def clear_buffer(self, session_id: str):
        """Clear the audio buffer for a specific session"""
        if session_id in self.active_sessions:
            self.active_sessions[session_id]["buffer"] = bytearray()
            return True
        return False