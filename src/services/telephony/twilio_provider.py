import logging
import base64
import audioop
import json
import asyncio
from typing import Dict, Optional
from fastapi import WebSocket
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect
from config.settings import settings
from services.voice.elevenlabs_service import elevenlabs_service

logger = logging.getLogger(__name__)

class TwilioProvider:
    """Twilio telephony provider implementation"""
    
    def __init__(self):
        self.client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        self._provider_name = "twilio"
    
    @property
    def provider_name(self) -> str:
        return self._provider_name
    
    async def initiate_call(
        self,
        from_number: str,
        to_number: str,
        webhook_url: str,
        **kwargs
    ) -> Dict:
        """Initiate outbound call via Twilio"""
        try:
            call = self.client.calls.create(
                from_=from_number,
                to=to_number,
                url=webhook_url,
                status_callback=kwargs.get('status_callback_url'),
                status_callback_event=['initiated', 'ringing', 'answered', 'completed'],
                status_callback_method='POST',
                record=kwargs.get('record', False),
                timeout=kwargs.get('timeout', 60)
            )
            
            logger.info(f"Twilio call initiated: {call.sid}")
            return {
                "call_sid": call.sid,
                "status": call.status,
                "provider": self.provider_name
            }
        except Exception as e:
            logger.error(f"Twilio call initiation failed: {str(e)}")
            raise
    
    def generate_connection_response(
        self,
        websocket_url: str,
        call_context: Dict
    ) -> str:
        """Generate TwiML response with WebSocket stream"""
        response = VoiceResponse()
        connect = Connect()
        
        # Add custom parameters to WebSocket URL
        ws_domain = settings.base_url.replace("https://", "").replace("http://", "")
        stream_url = f"wss://{ws_domain}{websocket_url}"
        
        logger.info(f"TwiML Stream URL: {stream_url}")
        connect.stream(url=stream_url)
        response.append(connect)
        
        return str(response)
    
    async def stream_audio_to_call(
        self,
        websocket: WebSocket,
        stream_id: str,
        text: str,
        stop_flag: dict,
        is_speaking_ref: dict
    ) -> None:
        """Stream ElevenLabs audio to Twilio with interruption handling"""
        if not stream_id:
            logger.error("No stream_id provided")
            return
        
        chunk_count = 0
        try:
            logger.info(f"Generating audio: {text[:50]}...")
            
            # Check stop flag before starting
            if stop_flag.get("stop", False):
                logger.warning("Stop flag already set - aborting audio")
                return
            
            # Stream audio chunks with interruption checking
            async for audio_chunk in elevenlabs_service.generate(text):
                # Check stop flag before every chunk
                if stop_flag.get("stop", False):
                    logger.warning(f"STOP FLAG DETECTED at chunk {chunk_count} - halting!")
                    try:
                        await websocket.send_json({"event": "clear", "streamSid": stream_id})
                        logger.info("CLEAR sent during chunk streaming")
                    except:
                        pass
                    return
                
                if audio_chunk and stream_id:
                    message = {
                        "event": "media",
                        "streamSid": stream_id,
                        "media": {"payload": audio_chunk}
                    }
                    await websocket.send_json(message)
                    chunk_count += 1
            
            logger.info(f"Sent {chunk_count} chunks to Twilio")
            
            # Wait for playback with interruption checking
            estimated_playback_seconds = (chunk_count * 0.02) + 0.5
            logger.info(f"Waiting {estimated_playback_seconds:.1f}s for Twilio playback")
            
            elapsed = 0
            check_interval = 0.05  # 50ms checks
            while elapsed < estimated_playback_seconds:
                if stop_flag.get("stop", False):
                    logger.warning(f"STOP during playback wait at {elapsed:.2f}s")
                    try:
                        await websocket.send_json({"event": "clear", "streamSid": stream_id})
                    except:
                        pass
                    return
                
                await asyncio.sleep(check_interval)
                elapsed += check_interval
            
            logger.info("🎵 Audio playback completed")
            
        except asyncio.CancelledError:
            logger.warning(f"Audio task CANCELLED at chunk {chunk_count}")
            try:
                await websocket.send_json({"event": "clear", "streamSid": stream_id})
                logger.info("CLEAR sent on task cancellation")
            except:
                pass
            raise
        except Exception as e:
            logger.error(f"Error streaming audio: {e}")
        finally:
            if is_speaking_ref:
                is_speaking_ref["speaking"] = False
                logger.debug("Speaking flag reset to FALSE")
    
    async def send_clear_command(
        self,
        websocket: WebSocket,
        stream_id: str
    ) -> None:
        """Send clear command to Twilio to stop audio playback"""
        if not stream_id:
            return
        
        try:
            clear_message = {"event": "clear", "streamSid": stream_id}
            await websocket.send_json(clear_message)
            logger.info("CLEAR command sent to Twilio")
        except Exception as e:
            logger.error(f"Error sending clear: {e}")
    
    async def convert_audio_payload(
        self,
        payload: str
    ) -> Optional[bytes]:
        """Convert Twilio's mulaw audio to linear PCM"""
        try:
            audio_data = base64.b64decode(payload)
            # Convert mulaw to linear16 (Twilio uses 8kHz mulaw)
            linear_audio = audioop.ulaw2lin(audio_data, 2)
            # Resample from 8kHz to 16kHz for Deepgram
            linear_audio = audioop.ratecv(linear_audio, 2, 1, 8000, 16000, None)[0]
            return linear_audio
        except Exception as e:
            logger.error(f"Audio conversion error: {e}")
            return None
    
    async def handle_media_stream(
        self,
        websocket: WebSocket,
        deepgram_service,
        stream_id: str,
        session_id: str
    ) -> None:
        """Handle Twilio media stream messages"""
        try:
            while True:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                data = json.loads(message)
                event = data.get("event")
                
                if event == "media":
                    payload = data.get("media", {}).get("payload")
                    if payload:
                        audio = await self.convert_audio_payload(payload, session_id)
                        if audio:
                            await deepgram_service.process_audio_chunk(session_id, audio)
                
                elif event == "stop":
                    logger.info(f"STREAM STOPPED")
                    break
                    
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.error(f"Media stream error: {e}")
