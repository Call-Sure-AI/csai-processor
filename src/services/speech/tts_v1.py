import logging
import aiohttp
import base64
import asyncio
from typing import Optional, AsyncGenerator
from config.settings import settings
import os
import time


logger = logging.getLogger(__name__)

class TextToSpeechService:
    def __init__(self):
        """Initialize ElevenLabs TTS Service"""
        self.voice_id = os.getenv("VOICE_ID","IKne3meq5aSn9XLyUdCD")
        self.api_key = os.getenv("ELEVEN_LABS_API_KEY")
        self.chunk_size = 32 * 1024  # 32KB per chunk for streaming
        self.chunk_delay = 0.01  # 10ms delay to prevent overloading
        
        # Validate configuration
        if not self.api_key:
            logger.warning("ElevenLabs API key is not set. TTS services will not work.")
    
    async def detect_language(self, text: str) -> str:
        """
        Detect language and select appropriate voice/model.
        This is a simplified implementation - consider using a more robust language detection library.
        """
        # Simple language detection heuristics
        if any('\u0380' <= char <= '\u03FF' for char in text):  # Greek characters
            return 'el'
        elif any('\u0590' <= char <= '\u05FF' for char in text):  # Hebrew characters
            return 'he'
        elif any('\u0600' <= char <= '\u06FF' for char in text):  # Arabic characters
            return 'ar'
        elif any('\u0400' <= char <= '\u04FF' for char in text):  # Cyrillic characters
            return 'ru'
        elif any('\u00C0' <= char <= '\u024F' for char in text):  # Extended Latin characters (covers most European languages)
            if any(char in text for char in 'áéíóúñ'):  # Spanish-specific characters
                return 'es'
            return 'en'
        return 'en'  # Default to English
    
        
    async def generate_audio(self, 
                            text: str, 
                            language: Optional[str] = None,
                            voice_settings: Optional[dict] = None) -> Optional[bytes]:
        """
        Converts text to speech using ElevenLabs API with performance optimizations.
        """
        start_time = time.time()
        try:
            # Log the start of TTS generation with timestamp
            logger.info(f"[TTS_SERVICE] Starting TTS generation at {start_time:.3f}")
            logger.info(f"[TTS_SERVICE] Text to convert (length {len(text)}): '{text[:50]}...'")
            
            # Detect language if not provided
            if not language:
                language = await self.detect_language(text)
                logger.info(f"[TTS_SERVICE] Detected language: {language}")
            
            # Select appropriate voice and model
            voice_config = {
                'en': {
                    'voice_id': 'eleven_monolingual_v1',
                    'model_id': 'eleven_turbo_v2_5'  # Using turbo for faster generation
                },
                # Other languages...
            }
            
            config = voice_config.get(language, voice_config.get('en'))
            logger.info(f"[TTS_SERVICE] Using voice: {config['voice_id']}, model: {config['model_id']}")
            
            # Default voice settings
            default_settings = {
                "stability": 0.3,
                "similarity_boost": 0.5,
                "style": 0.0,  # Reduced style for faster generation
                "use_speaker_boost": True
            }
            
            # Merge custom settings with defaults
            voice_settings = {**default_settings, **(voice_settings or {})}
            
            url = f"https://api.elevenlabs.io/v1/text-to-speech/IKne3meq5aSn9XLyUdCD"
            headers = {
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": self.api_key
            }

            payload = {
                "text": text,
                "model_id": config['model_id'],
                "voice_settings": voice_settings
            }

            logger.info(f"[TTS_SERVICE] Sending TTS API request at {time.time()-start_time:.3f}s")

            async with aiohttp.ClientSession() as session:
                request_start = time.time()
                async with session.post(
                    url, 
                    json=payload, 
                    headers=headers, 
                    timeout=aiohttp.ClientTimeout(total=5)  # Reduced timeout
                ) as response:
                    request_time = time.time() - request_start
                    logger.info(f"[TTS_SERVICE] API request took {request_time:.3f}s")
                    
                    response_data = await response.read()
                    total_time = time.time() - start_time
                    
                    if response.status == 200:
                        logger.info(f"[TTS_SERVICE] Success! Received {len(response_data)} bytes in {total_time:.3f}s")
                        return response_data
                    else:
                        error_message = await response.text()
                        logger.error(f"[TTS_SERVICE] API error: {response.status} - {error_message} after {total_time:.3f}s")
                        return None
        
        except asyncio.TimeoutError:
            logger.error(f"[TTS_SERVICE] Request timed out after {time.time()-start_time:.3f}s")
            return None
        except Exception as e:
            logger.error(f"[TTS_SERVICE] Error in TTS generation: {str(e)} after {time.time()-start_time:.3f}s", exc_info=True)
            return None
    
    
    async def stream_text_to_speech(self, 
                                    text: str, 
                                    language: Optional[str] = None,
                                    voice_settings: Optional[dict] = None) -> AsyncGenerator[bytes, None]:
        """
        Streams audio response from ElevenLabs API.

        Args:
            text (str): The input text to be converted into speech.
            language (str, optional): Language code
            voice_settings (dict, optional): Custom voice settings
        
        Yields:
            bytes: Audio chunks as they arrive.
        """
        try:
            # Detect language if not provided
            if not language:
                language = await self.detect_language(text)
            
            # Select appropriate voice and model
            voice_config = {
                'en': {
                    'voice_id': 'eleven_monolingual_v1',
                    'model_id': 'eleven_turbo_v2_5'
                },
                'es': {
                    'voice_id': 'eleven_multilingual_v2',
                    'model_id': 'eleven_multilingual_v2'
                },
                'default': {
                    'voice_id': 'eleven_multilingual_v2',
                    'model_id': 'eleven_multilingual_v2'
                }
            }
            
            # Select voice configuration
            config = voice_config.get(language, voice_config['default'])
            
            # Default voice settings
            default_settings = {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "style": 0.2,
                "use_speaker_boost": True
            }
            
            # Merge custom settings with defaults
            voice_settings = {**default_settings, **(voice_settings or {})}
            
            url = f"https://api.elevenlabs.io/v1/text-to-speech/IKne3meq5aSn9XLyUdCD/stream"
            headers = {
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": self.api_key
            }

            payload = {
                "text": text,
                "model_id": config['model_id'],
                "voice_settings": voice_settings
            }

            logger.info(f"Starting TTS stream: language={language}, voice={config['voice_id']}, text_length={len(text)}")

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        logger.info(f"[TTS_STREAM_DEBUG] Audio stream started: content-type={response.headers.get('content-type')}")
                        
                        # Use an async generator to stream chunks
                        async for chunk in response.content.iter_chunked(self.chunk_size):
                            yield chunk
                            await asyncio.sleep(self.chunk_delay)
                    else:
                        error_message = await response.text()
                        logger.error(f"ElevenLabs streaming error: {response.status} - {error_message}")
        
        except asyncio.TimeoutError:
            logger.error("TTS streaming request timed out")
        except Exception as e:
            logger.error(f"Unexpected error in TTS streaming: {str(e)}", exc_info=True)

    # async def convert_audio_for_twilio(self, audio_bytes: bytes) -> Optional[bytes]:
    #     """
    #     Convert generated audio to Twilio-compatible μ-law format.
        
    #     Args:
    #         audio_bytes (bytes): Input audio bytes
        
    #     Returns:
    #         Optional[bytes]: Converted audio bytes or None if conversion fails
    #     """
    #     try:
    #         import subprocess
    #         import tempfile
            
    #         # Create temporary input and output files
    #         with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as input_file, \
    #              tempfile.NamedTemporaryFile(suffix='.raw', delete=False) as output_file:
                
    #             input_file.write(audio_bytes)
    #             input_file.flush()
                
    #             # FFmpeg command to convert to Twilio-compatible μ-law
    #             ffmpeg_command = [
    #                 'ffmpeg', 
    #                 '-y',                   # Overwrite output file
    #                 '-i', input_file.name,  # Input file
    #                 '-ar', '8000',          # Set sample rate to 8kHz
    #                 '-ac', '1',             # Mono channel
    #                 '-acodec', 'pcm_mulaw', # μ-law encoding
    #                 '-f', 'mulaw',          # μ-law format
    #                 output_file.name        # Output file
    #             ]
                
    #             # Run FFmpeg conversion
    #             result = subprocess.run(
    #                 ffmpeg_command, 
    #                 capture_output=True, 
    #                 text=True
    #             )
                
    #             # Check conversion result
    #             if result.returncode == 0:
    #                 with open(output_file.name, 'rb') as f:
    #                     converted_audio = f.read()
                    
    #                 logger.info(f"Audio conversion successful: {len(converted_audio)} bytes")
    #                 return converted_audio
    #             else:
    #                 logger.error(f"FFmpeg conversion failed: {result.stderr}")
    #                 return None
        
    #     except Exception as e:
    #         logger.error(f"Error converting audio for Twilio: {str(e)}", exc_info=True)
    #         return None
    #     finally:
    #         # Clean up temporary files
    #         import os
    #         for filename in [input_file.name, output_file.name]:
    #             try:
    #                 os.unlink(filename)
    #             except:
    #                 pass
                
                

    async def convert_audio_for_twilio(self, audio_bytes: bytes) -> Optional[bytes]:
        """
        Convert generated audio to Twilio-compatible μ-law format with optimized pipeline.
        """
        try:
            import subprocess
            
            # FFmpeg command using pipes for faster I/O
            ffmpeg_command = [
                'ffmpeg', 
                '-y',                   # Overwrite output
                '-f', 'mp3',            # Input format
                '-i', 'pipe:0',         # Read from stdin
                '-ar', '8000',          # Set sample rate to 8kHz
                '-ac', '1',             # Mono channel
                '-acodec', 'pcm_mulaw', # μ-law encoding
                '-f', 'mulaw',          # μ-law format
                'pipe:1'                # Output to stdout
            ]
            
            # Run FFmpeg with pipes
            process = subprocess.run(
                ffmpeg_command,
                input=audio_bytes,      # Send MP3 to stdin
                capture_output=True,    # Capture stdout
                check=True
            )
            
            converted_audio = process.stdout
            
            if converted_audio:
                logger.info(f"Audio conversion successful: {len(converted_audio)} bytes")
                return converted_audio
            else:
                logger.error("FFmpeg conversion produced no output")
                return None
        
        except Exception as e:
            logger.error(f"Error converting audio for Twilio: {str(e)}", exc_info=True)
            return None