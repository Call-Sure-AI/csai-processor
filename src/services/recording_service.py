import logging
from typing import Optional
from twilio.rest import Client

from config.settings import settings

logger = logging.getLogger(__name__)


class RecordingService:
    """Service for managing call recordings"""
    
    def __init__(self):
        if settings.twilio_account_sid and settings.twilio_auth_token:
            self.client = Client(
                settings.twilio_account_sid,
                settings.twilio_auth_token
            )
        else:
            self.client = None
            logger.warning("Twilio credentials not configured - recording service unavailable")
    
    async def start_recording(
        self,
        call_sid: str,
        tts_service: Optional[any] = None
    ) -> Optional[str]:
        """
        Start recording for a call
        
        Args:
            call_sid: Twilio call SID
            tts_service: Optional TTS service to announce recording
            
        Returns:
            Recording SID if successful, None otherwise
        """
        try:
            if not settings.recording_enabled:
                logger.info("Recording is disabled in settings")
                return None
            
            if not self.client:
                logger.error("Recording service not initialized - Twilio client missing")
                return None
            
            # Announce recording if TTS service provided
            if tts_service:
                try:
                    await tts_service.generate(
                        {
                            'partialResponseIndex': None,
                            'partialResponse': 'This call will be recorded.'
                        },
                        0
                    )
                except Exception as e:
                    logger.warning(f"Failed to announce recording: {e}")
            
            # Start recording with dual channel (separate tracks for caller and agent)
            recording = self.client.calls(call_sid).recordings.create(
                recording_channels='dual'
            )
            
            logger.info(f"Recording Created: {recording.sid}".upper())
            return recording.sid
            
        except Exception as e:
            logger.error(f"Error starting recording: {e}")
            return None
    
    async def stop_recording(self, call_sid: str, recording_sid: str) -> bool:
        """
        Stop an active recording
        
        Args:
            call_sid: Twilio call SID
            recording_sid: Recording SID to stop
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if not self.client:
                logger.error("Recording service not initialized")
                return False
            
            self.client.calls(call_sid).recordings(recording_sid).update(
                status='stopped'
            )
            
            logger.info(f"Recording Stopped: {recording_sid}")
            return True
            
        except Exception as e:
            logger.error(f"Error stopping recording: {e}")
            return False
    
    async def get_recording_url(self, recording_sid: str) -> Optional[str]:
        """
        Get recording URL
        
        Args:
            recording_sid: Recording SID
            
        Returns:
            Recording URL if found, None otherwise
        """
        try:
            if not self.client:
                logger.error("Recording service not initialized")
                return None
            
            recording = self.client.recordings(recording_sid).fetch()
            
            # Construct full URL
            base_url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}"
            recording_url = f"{base_url}/Recordings/{recording.sid}.mp3"
            
            return recording_url
            
        except Exception as e:
            logger.error(f"Error getting recording URL: {e}")
            return None
    
    async def delete_recording(self, recording_sid: str) -> bool:
        """
        Delete a recording
        
        Args:
            recording_sid: Recording SID to delete
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if not self.client:
                logger.error("Recording service not initialized")
                return False
            
            self.client.recordings(recording_sid).delete()
            
            logger.info(f"Recording Deleted: {recording_sid}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting recording: {e}")
            return False


# Global recording service instance
recording_service = RecordingService()

async def start_call_recording(
    tts_service: Optional[any],
    call_sid: str
) -> Optional[str]:
    """
    Convenience function to start recording
    
    Args:
        tts_service: TTS service instance
        call_sid: Twilio call SID
        
    Returns:
        Recording SID if successful, None otherwise
    """
    return await recording_service.start_recording(call_sid, tts_service)
