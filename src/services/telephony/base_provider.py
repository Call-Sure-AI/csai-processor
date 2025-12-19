from abc import ABC, abstractmethod
from typing import Dict, Optional, AsyncGenerator
from fastapi import WebSocket
from sqlalchemy.orm import Session

class BaseTelephonyProvider(ABC):
    """Abstract base class for telephony providers"""
    
    @abstractmethod
    async def initiate_call(
        self,
        from_number: str,
        to_number: str,
        webhook_url: str,
        **kwargs
    ) -> Dict:
        """Initiate an outbound call"""
        pass
    
    @abstractmethod
    def generate_connection_response(
        self,
        websocket_url: str,
        call_context: Dict
    ) -> str:
        """Generate provider-specific TwiML/XML response for WebSocket connection"""
        pass
    
    @abstractmethod
    async def handle_media_stream(
        self,
        websocket: WebSocket,
        deepgram_service,
        elevenlabs_service,
        call_context: Dict
    ) -> None:
        """Handle real-time media streaming"""
        pass
    
    @abstractmethod
    async def stream_audio_to_call(
        self,
        websocket: WebSocket,
        stream_id: str,
        text: str,
        stop_flag: dict,
        is_speaking_ref: dict
    ) -> None:
        """Stream audio to the call"""
        pass
    
    @abstractmethod
    async def send_clear_command(
        self,
        websocket: WebSocket,
        stream_id: str
    ) -> None:
        """Send command to clear audio buffer"""
        pass
    
    @abstractmethod
    async def convert_audio_payload(
        self,
        payload: str
    ) -> Optional[bytes]:
        """Convert provider-specific audio format to raw PCM"""
        pass
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider name"""
        pass
