import logging
import base64
import json
import asyncio
import httpx
from typing import Dict, Optional
from fastapi import WebSocket
from config.settings import settings
from services.voice.elevenlabs_service import elevenlabs_service

logger = logging.getLogger(__name__)

class ExotelProvider:
    """Exotel telephony provider implementation"""
    
    def __init__(self):
        self.api_key = settings.exotel_api_key
        self.api_token = settings.exotel_api_token
        self.sid = settings.exotel_sid
        self.base_url = f"https://api.exotel.com/v1/Accounts/{self.sid}"
        self._provider_name = "exotel"
    
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
        """Initiate outbound call via Exotel"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/Calls/connect.json",
                    auth=(self.api_key, self.api_token),
                    data={
                        "From": from_number,
                        "To": to_number,
                        "Url": webhook_url,
                        "CallerId": kwargs.get('caller_id', from_number),
                        "CallType": kwargs.get('call_type', 'trans'),
                        "StatusCallback": kwargs.get('status_callback_url'),
                        "TimeLimit": kwargs.get('timeout', 3600),
                        "TimeOut": kwargs.get('ring_timeout', 30),
                        "Record": "true" if kwargs.get('record', False) else "false"
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    call_sid = data.get("Call", {}).get("Sid")
                    logger.info(f"Exotel call initiated: {call_sid}")
                    return {
                        "call_sid": call_sid,
                        "status": data.get("Call", {}).get("Status"),
                        "provider": self.provider_name
                    }
                else:
                    raise Exception(f"Exotel API error: {response.status_code} - {response.text}")
                    
        except Exception as e:
            logger.error(f"Exotel call initiation failed: {str(e)}")
            raise
    
    def generate_connection_response(
        self,
        websocket_url: str,
        call_context: Dict
    ) -> str:
        """Generate Exotel XML response with WebSocket stream"""
        ws_domain = settings.base_url.replace("https://", "").replace("http://", "")
        stream_url = f"wss://{ws_domain}{websocket_url}"
        
        # Exotel uses similar TwiML-like XML
        response_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_url}">
            <Parameter name="callSid" value="{call_context.get('call_sid', '')}" />
            <Parameter name="companyId" value="{call_context.get('company_id', '')}" />
        </Stream>
    </Connect>
</Response>"""
        
        logger.info(f"Exotel Stream URL: {stream_url}")
        return response_xml
    
    async def stream_audio_to_call(
        self,
        websocket: WebSocket,
        stream_id: str,
        text: str,
        stop_flag: dict,
        is_speaking_ref: dict
    ) -> None:
        """Stream ElevenLabs audio to Exotel with interruption handling"""
        if not stream_id:
            logger.error("No stream_id provided")
            return
        
        chunk_count = 0
        try:
            logger.info(f"🎤 Generating audio: {text[:50]}...")
            
            if stop_flag.get("stop", False):
                logger.warning("Stop flag already set - aborting audio")
                return
            
            async for audio_chunk in elevenlabs_service.generate(text):
                if stop_flag.get("stop", False):
                    logger.warning(f"STOP FLAG DETECTED at chunk {chunk_count}")
                    try:
                        await websocket.send_json({"event": "clear", "streamSid": stream_id})
                    except:
                        pass
                    return
                
                if audio_chunk and stream_id:
                    # Exotel uses similar format to Twilio
                    message = {
                        "event": "media",
                        "streamSid": stream_id,
                        "media": {"payload": audio_chunk}
                    }
                    await websocket.send_json(message)
                    chunk_count += 1
            
            logger.info(f"Sent {chunk_count} chunks to Exotel")
            
            # Wait for playback
            estimated_playback_seconds = (chunk_count * 0.02) + 0.5
            elapsed = 0
            check_interval = 0.05
            
            while elapsed < estimated_playback_seconds:
                if stop_flag.get("stop", False):
                    logger.warning(f"STOP during playback")
                    try:
                        await websocket.send_json({"event": "clear", "streamSid": stream_id})
                    except:
                        pass
                    return
                
                await asyncio.sleep(check_interval)
                elapsed += check_interval
            
            logger.info("Audio playback completed")
            
        except asyncio.CancelledError:
            logger.warning(f"Audio task CANCELLED")
            try:
                await websocket.send_json({"event": "clear", "streamSid": stream_id})
            except:
                pass
            raise
        except Exception as e:
            logger.error(f"Error streaming audio: {e}")
        finally:
            if is_speaking_ref:
                is_speaking_ref["speaking"] = False
    
    async def send_clear_command(
        self,
        websocket: WebSocket,
        stream_id: str
    ) -> None:
        """Send clear command to Exotel"""
        if not stream_id:
            return
        
        try:
            clear_message = {"event": "clear", "streamSid": stream_id}
            await websocket.send_json(clear_message)
            logger.info("CLEAR command sent to Exotel")
        except Exception as e:
            logger.error(f"Error sending clear: {e}")
    
    async def convert_audio_payload(
        self,
        payload: str
    ) -> Optional[bytes]:
        """Convert Exotel's audio format to linear PCM"""
        try:
            # Exotel also uses mulaw like Twilio
            audio_data = base64.b64decode(payload)
            import audioop
            linear_audio = audioop.ulaw2lin(audio_data, 2)
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
        """Handle Exotel media stream messages"""
        try:
            while True:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.5)
                data = json.loads(message)
                event = data.get("event")
                
                if event == "media":
                    payload = data.get("media", {}).get("payload")
                    if payload:
                        audio = await self.convert_audio_payload(payload)
                        if audio:
                            await deepgram_service.process_audio_chunk(session_id, audio)
                
                elif event == "stop":
                    logger.info(f"STREAM STOPPED")
                    break
                    
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.error(f"Media stream error: {e}")
