"""
Twilio Webhook Routes for Call Handling
"""
from typing import Optional, Dict, Any
from fastapi import APIRouter, Request, Form, HTTPException, WebSocket
from fastapi.responses import Response
from loguru import logger
from datetime import datetime

from services.speech.realtime_stream_service import RealTimeSTTHandler, RealTimeTTSHandler
from services.speech.media_stream_service import TwilioMediaStreamHandler
from services.voice.twilio_service import twilio_service
from services.speech.conversation_manager_service import create_conversation_manager

from config.settings import settings


import hmac
import hashlib
import base64

router = APIRouter()

def generate_twiml_response(
    message: str = "Hello! This is a test call from your Celery Twilio service.",
    voice: str = "alice",
    language: str = "en-US",
    gather_input: bool = False,
    gather_timeout: int = 10,
    gather_num_digits: int = 1,
    action_url: Optional[str] = None,
    method: str = "POST"
) -> str:
    """
    Generate TwiML response for voice calls
    
    Args:
        message: Text to be spoken
        voice: Voice to use (alice, man, woman)
        language: Language code (en-US, en-GB, etc.)
        gather_input: Whether to gather user input
        gather_timeout: Timeout for gathering input in seconds
        gather_num_digits: Number of digits to gather
        action_url: URL to send gathered input to
        method: HTTP method for action URL
    
    Returns:
        TwiML XML string
    """
    
    if gather_input:
        # Create TwiML with Gather for user input
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
            <Response>
                <Gather timeout="{gather_timeout}" numDigits="{gather_num_digits}" action="{action_url or ''}" method="{method}">
                    <Say voice="{voice}" language="{language}">{message}</Say>
                </Gather>
                <Say voice="{voice}" language="{language}">We didn't receive any input. Goodbye!</Say>
            </Response>"""
    else:
        # Simple TwiML with just speech
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
            <Response>
                <Say voice="{voice}" language="{language}">{message}</Say>
            </Response>"""
    
    return twiml

class TwilioWebhookHandler:
    """Handles Twilio webhooks for call events"""
    
    def __init__(self, twilio_service, conversation_manager):
        self.twilio_service = twilio_service
        self.conversation_manager = conversation_manager
        
    async def handle_incoming_call(self, request: Request) -> Response:
        """Handle incoming call webhook"""
        form_data = await request.form()
        
        # Validate webhook signature
        if not self._validate_signature(request, form_data):
            raise HTTPException(status_code=403, detail="Invalid signature")
            
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        # Generate TwiML response for WebRTC streaming
        twiml = await self.twilio_service.handle_incoming_call(
            call_sid=call_sid,
            from_number=from_number,
            to_number=to_number,
            company_api_key=request.headers.get("X-Company-Key"),
            agent_id=form_data.get("agent_id", "default"),
            base_url=str(request.base_url)
        )
        
        return Response(content=str(twiml), media_type="text/xml")
        
    async def handle_call_status(self, request: Request) -> Dict:
        """Handle call status updates"""
        form_data = await request.form()
        
        call_sid = form_data.get("CallSid")
        call_status = form_data.get("CallStatus")
        
        await self.twilio_service.update_call_status(call_sid, call_status)
        
        # Handle specific status changes
        if call_status == "completed":
            await self._handle_call_completed(call_sid)
        elif call_status == "failed":
            await self._handle_call_failed(call_sid)
            
        return {"status": "ok"}
        
    async def _handle_call_completed(self, call_sid: str):
        """Process completed call"""
        # Generate call summary
        if call_sid in self.conversation_manager.active_conversations:
            context = self.conversation_manager.active_conversations[call_sid]
            
            # Save conversation transcript
            await self._save_transcript(call_sid, context)
            
            # Generate and save summary
            summary = await self._generate_call_summary(context)
            await self._save_summary(call_sid, summary)
            
            # Clean up
            del self.conversation_manager.active_conversations[call_sid]
            
    def _validate_signature(self, request: Request, form_data: Dict) -> bool:
        """Validate Twilio webhook signature"""
        signature = request.headers.get("X-Twilio-Signature", "")
        auth_token = settings.twilio_auth_token
        url = str(request.url)
        
        # Create signature string
        s = url
        if form_data:
            for key in sorted(form_data.keys()):
                s += key + form_data[key]
                
        # Calculate expected signature
        mac = hmac.new(
            auth_token.encode('utf-8'),
            s.encode('utf-8'),
            hashlib.sha1
        )
        expected = base64.b64encode(mac.digest()).decode('utf-8')
        
        return hmac.compare_digest(expected, signature)
    
    async def _save_transcript(self, call_sid: str, context):
        """Save conversation transcript"""
        # TODO: Implement transcript saving
        logger.info(f"Saving transcript for call {call_sid}")
        
    async def _generate_call_summary(self, context):
        """Generate call summary"""
        # TODO: Implement call summary generation
        logger.info(f"Generating summary for call {context.call_sid}")
        return "Call completed successfully"
        
    async def _save_summary(self, call_sid: str, summary: str):
        """Save call summary"""
        # TODO: Implement summary saving
        logger.info(f"Saving summary for call {call_sid}: {summary}")
        
    async def _handle_call_failed(self, call_sid: str):
        """Handle failed call"""
        logger.error(f"Call {call_sid} failed")
        # TODO: Implement failed call handling

# API Routes
@router.post("/incoming-call")
async def incoming_call(request: Request):
    """Handle incoming call webhook"""
    # Create conversation manager with real agent config
    agent_config = {
        "name": "Default Agent",
        "greeting": "Hello! How can I help you today?",
        "voice": "alice",
        "language": "en-US",
        "call_type": "support"  # Default call type
    }
    
    conversation_manager = create_conversation_manager(agent_config)
    handler = TwilioWebhookHandler(twilio_service, conversation_manager)
    return await handler.handle_incoming_call(request)

@router.post("/status")
async def call_status(request: Request):
    """Handle call status webhook"""
    # Create conversation manager with real agent config
    agent_config = {
        "name": "Default Agent",
        "greeting": "Hello! How can I help you today?",
        "voice": "alice",
        "language": "en-US",
        "call_type": "support"  # Default call type
    }
    
    conversation_manager = create_conversation_manager(agent_config)
    handler = TwilioWebhookHandler(twilio_service, conversation_manager)
    return await handler.handle_call_status(request)

@router.websocket("/webrtc/twilio-stream/{peer_id}/{company_key}/{agent_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    peer_id: str,
    company_key: str,
    agent_id: str
):
    """WebSocket endpoint for Twilio media streaming"""
    await websocket.accept()
    
    # Get call info from peer_id mapping
    call_info = twilio_service.get_call_by_peer_id(peer_id)
    
    if not call_info:
        await websocket.close(code=1008, reason="Invalid peer ID")
        return
        
    # Initialize stream handler
    stream_handler = TwilioMediaStreamHandler(
        call_sid=call_info["call_sid"],
        stream_sid=None
    )
    
    # Set up components
    stream_handler.stt_handler = RealTimeSTTHandler()
    stream_handler.tts_handler = RealTimeTTSHandler()
    
    # Create conversation manager for this stream with real agent config
    agent_config = {
        "name": f"Agent {agent_id}",
        "greeting": "Hello! How can I help you today?",
        "voice": "alice",
        "language": "en-US",
        "call_type": "support",
        "company_key": company_key,
        "agent_id": agent_id
    }
    
    stream_handler.conversation_manager = create_conversation_manager(agent_config)
    
    # Initialize components
    await stream_handler.stt_handler.initialize()
    await stream_handler.tts_handler.initialize()
    
    # Handle WebSocket connection
    await stream_handler.handle_websocket(websocket, None)