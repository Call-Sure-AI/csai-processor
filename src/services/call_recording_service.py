import logging
import json
from typing import Optional, Dict, List
from datetime import datetime
import httpx
from handlers.s3_handler import S3Handler
from config.settings import settings

logger = logging.getLogger(__name__)


class CallRecordingService:
    """Service to handle call recordings and transcript uploads to S3"""
    
    def __init__(self):
        try:
            self.s3_handler = S3Handler()
            self.s3_enabled = True
            logger.info("Call Recording Service initialized with S3")
        except Exception as e:
            logger.warning(f"S3 not configured: {str(e)}")
            self.s3_handler = None
            self.s3_enabled = False
        
        self.twilio_account_sid = settings.twilio_account_sid
        self.twilio_auth_token = settings.twilio_auth_token
        
    async def save_call_data(
        self,
        call_sid: str,
        company_id: str,
        agent_id: str,
        transcript: List[Dict],
        recording_url: Optional[str] = None,
        duration: int = 0,
        from_number: str = None,
        to_number: str = None
    ) -> Dict[str, str]:

        try:
            if not self.s3_enabled:
                logger.warning("S3 not enabled, skipping upload")
                return {'transcript_url': None, 'recording_url': None}
            
            result = {
                'transcript_url': None,
                'recording_url': None
            }
            
            # 1. Upload transcript JSON
            if transcript and len(transcript) > 0:
                transcript_url = await self._upload_transcript(
                    call_sid=call_sid,
                    company_id=company_id,
                    agent_id=agent_id,
                    transcript=transcript,
                    duration=duration,
                    from_number=from_number,
                    to_number=to_number
                )
                if transcript_url:
                    result['transcript_url'] = transcript_url
                    logger.info(f"Transcript uploaded: {transcript_url}")
            else:
                logger.warning(f"No transcript to upload for call {call_sid}")
            
            # 2. Download and upload Twilio recording (if available)
            if recording_url:
                recording_s3_url = await self._upload_recording(
                    call_sid=call_sid,
                    company_id=company_id,
                    recording_url=recording_url
                )
                if recording_s3_url:
                    result['recording_url'] = recording_s3_url
                    logger.info(f"Recording uploaded: {recording_s3_url}")
            else:
                logger.info(f"No recording URL provided for call {call_sid}")
            
            return result
            
        except Exception as e:
            logger.error(f"Error saving call data: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return {'transcript_url': None, 'recording_url': None}
    
    async def _upload_transcript(
        self,
        call_sid: str,
        company_id: str,
        agent_id: str,
        transcript: List[Dict],
        duration: int,
        from_number: str = None,
        to_number: str = None
    ) -> Optional[str]:
        """Upload transcript JSON to S3"""
        try:
            # Format transcript data
            transcript_data = {
                'call_sid': call_sid,
                'company_id': company_id,
                'agent_id': agent_id,
                'from_number': from_number,
                'to_number': to_number,
                'duration_seconds': duration,
                'timestamp': datetime.utcnow().isoformat(),
                'conversation_turns': len(transcript),
                'conversation': transcript
            }
            
            # Generate S3 key with date-based path
            date_path = datetime.utcnow().strftime('%Y/%m/%d')
            s3_key = f"call-transcripts/{company_id}/{date_path}/{call_sid}.json"
            
            # Convert to JSON bytes
            json_content = json.dumps(transcript_data, indent=2, ensure_ascii=False)
            json_bytes = json_content.encode('utf-8')
            
            # Create a file-like object for upload
            from io import BytesIO
            file_obj = BytesIO(json_bytes)
            
            # Create a mock UploadFile
            class MockUploadFile:
                def __init__(self, content: bytes, filename: str, content_type: str):
                    self._content = content
                    self.filename = filename
                    self.content_type = content_type
                    self._position = 0
                
                async def read(self, size: int = -1) -> bytes:
                    if size == -1:
                        result = self._content[self._position:]
                        self._position = len(self._content)
                    else:
                        result = self._content[self._position:self._position + size]
                        self._position += size
                    return result
                
                async def seek(self, position: int) -> int:
                    self._position = position
                    return self._position
            
            mock_file = MockUploadFile(
                content=json_bytes,
                filename=f"{call_sid}.json",
                content_type='application/json'
            )
            
            # Upload to S3
            upload_result = await self.s3_handler.upload_file(
                file=mock_file,
                enable_public_read_access=False,
                custom_key=s3_key
            )
            
            if upload_result.get('success'):
                return upload_result.get('url')
            else:
                logger.error(f"S3 upload failed: {upload_result.get('error')}")
                return None
            
        except Exception as e:
            logger.error(f"Error uploading transcript: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def _upload_recording(
        self,
        call_sid: str,
        company_id: str,
        recording_url: str
    ) -> Optional[str]:
        """Download Twilio recording and upload to S3"""
        try:
            # Download recording from Twilio
            logger.info(f"Downloading recording from Twilio: {recording_url}")
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    recording_url,
                    auth=(self.twilio_account_sid, self.twilio_auth_token),
                    timeout=60.0,
                    follow_redirects=True
                )
                
                if response.status_code != 200:
                    logger.error(f"Failed to download recording: HTTP {response.status_code}")
                    return None
                
                audio_content = response.content
                content_type = response.headers.get('Content-Type', 'audio/mpeg')
                
                logger.info(f"Downloaded {len(audio_content)} bytes, content-type: {content_type}")
                
                # Determine file extension from content type
                extension = 'mp3'
                if 'wav' in content_type.lower():
                    extension = 'wav'
                elif 'mp4' in content_type.lower():
                    extension = 'mp4'
                
                # Generate S3 key with date-based path
                date_path = datetime.utcnow().strftime('%Y/%m/%d')
                s3_key = f"call-recordings/{company_id}/{date_path}/{call_sid}.{extension}"
                
                # Create a mock UploadFile
                class MockUploadFile:
                    def __init__(self, content: bytes, filename: str, content_type: str):
                        self._content = content
                        self.filename = filename
                        self.content_type = content_type
                        self._position = 0
                    
                    async def read(self, size: int = -1) -> bytes:
                        if size == -1:
                            result = self._content[self._position:]
                            self._position = len(self._content)
                        else:
                            result = self._content[self._position:self._position + size]
                            self._position += size
                        return result
                    
                    async def seek(self, position: int) -> int:
                        self._position = position
                        return self._position
                
                mock_file = MockUploadFile(
                    content=audio_content,
                    filename=f"{call_sid}.{extension}",
                    content_type=content_type
                )
                
                # Upload to S3
                upload_result = await self.s3_handler.upload_file(
                    file=mock_file,
                    enable_public_read_access=False,
                    custom_key=s3_key
                )
                
                if upload_result.get('success'):
                    return upload_result.get('url')
                else:
                    logger.error(f"S3 upload failed: {upload_result.get('error')}")
                    return None
                
        except httpx.TimeoutException:
            logger.error("Timeout downloading recording from Twilio")
            return None
        except Exception as e:
            logger.error(f"Error uploading recording: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def get_recording_url_from_twilio(self, call_sid: str) -> Optional[str]:
        """
        Get recording URL from Twilio API
        
        Note: Recordings may take a few seconds to be available after call ends
        """
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_account_sid}/Recordings.json?CallSid={call_sid}"
            
            logger.info(f"🔍 Fetching recording URL for call {call_sid}")
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    auth=(self.twilio_account_sid, self.twilio_auth_token),
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    recordings = data.get('recordings', [])
                    
                    if recordings:
                        # Get the most recent recording
                        recording_sid = recordings[0]['sid']
                        recording_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_account_sid}/Recordings/{recording_sid}.mp3"
                        logger.info(f"Found recording: {recording_sid}")
                        return recording_url
                    else:
                        logger.info(f"No recordings found yet for call {call_sid}")
                else:
                    logger.error(f"Twilio API error: HTTP {response.status_code}")
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting recording URL: {str(e)}")
            return None


# Global instance
call_recording_service = CallRecordingService()
