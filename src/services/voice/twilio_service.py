# src\services\voice\twilio_service.py
from typing import Dict, Any, Optional, Callable
import logging
import asyncio
import json
import uuid
from datetime import datetime
from twilio.rest import Client
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse, Connect, Start
from config.settings import settings
from sqlalchemy.orm import Session
from database.models import Call, CallEvent, CallType
from sqlalchemy import func

logger = logging.getLogger(__name__)

def _upsert_call(db: Session, call_sid: str, **fields):
    """Create or update a call record"""
    call = db.query(Call).filter(Call.call_sid == call_sid).one_or_none()
    if not call:
        call = Call(
            call_sid=call_sid,
            call_type=CallType.INCOMING
        )
        db.add(call)

    for k, v in fields.items():
        setattr(call, k, v)
    return call

class TwilioVoiceService:
    """Twilio Voice API service with comprehensive call management"""
    
    def __init__(self):
        self.client: Optional[Client] = None
        self.validator: Optional[RequestValidator] = None
        self.active_calls: Dict[str, Dict[str, Any]] = {}
        self.call_handlers: Dict[str, Callable] = {}
        self._initialized = False
        
    async def initialize(self):
        """Initialize Twilio client and services"""
        if self._initialized:
            return
            
        try:
            if settings.twilio_account_sid and settings.twilio_auth_token:
                self.client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
                self.validator = RequestValidator(settings.twilio_auth_token)
                self._initialized = True
                logger.info("Twilio Voice service initialized successfully")
            else:
                logger.warning("Missing Twilio credentials - service disabled")
        except Exception as e:
            logger.error(f"Failed to initialize Twilio service: {str(e)}")
            
    def validate_request(self, request_signature: str, request_url: str, params: Dict[str, Any]) -> bool:
        """Validate incoming Twilio webhook requests"""
        if not self.validator:
            return False
        return self.validator.validate(request_signature, request_url, params)
        
    async def create_call(
        self, 
        to_number: str, 
        from_number: str, 
        webhook_url: str,
        status_callback_url: Optional[str] = None,
        db_session: Optional[Session] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Initiate an outbound call"""
        if not self.client:
            raise RuntimeError("Twilio client not initialized")
            
        try:
            call_params = {
                'to': to_number,
                'from_': from_number if from_number else settings.twilio_phone_number,
                'url': webhook_url,
                'status_callback': status_callback_url,
                'status_callback_method': 'POST',
                'status_callback_event': ['initiated', 'ringing', 'answered', 'completed'],
            }
            
            # Add any additional kwargs
            if kwargs:
                call_params.update(kwargs)
            
            logger.info(f"Call params: {call_params}")
            
            call = self.client.calls.create(**call_params)
            
            call_data = {
                'call_sid': call.sid,
                'status': call.status,
                'direction': call.direction,
                'to': call.to,
                'from': getattr(call, 'from_', getattr(call, 'from_number', from_number)),
                'created_at': datetime.utcnow(),
                # 'metadata': kwargs.get('metadata', {})
            }
            
            self.active_calls[call.sid] = call_data
            logger.info(f"Created outbound call: {call.sid}")
            db: Session = kwargs.get("db_session")
            if db:
                _call = _upsert_call(
                    db,
                    call_sid=call.sid,
                    direction=call.direction or "outbound-api",
                    from_number=call_data["from"],
                    to_number=call.to,
                    status=call.status,
                )
                db.add(CallEvent(call=_call, call_sid=call.sid, event="initiated"))
                db.commit()
            return call_data
            
        except Exception as e:
            logger.error(f"Failed to create call: {str(e)}")
            raise
            
    async def handle_incoming_call(
        self, 
        call_sid: str, 
        from_number: str, 
        to_number: str,
        company_api_key: str,
        agent_id: str,
        base_url: str,
        db_session: Optional[Session] = None,
        **kwargs
    ) -> VoiceResponse:
        """Handle incoming call and generate TwiML response"""
        try:
            # Generate unique peer ID for WebRTC connection
            peer_id = f"twilio_{call_sid}_{uuid.uuid4().hex[:8]}"
            
            # Store call mapping
            self.active_calls[call_sid] = {
                'call_sid': call_sid,
                'peer_id': peer_id,
                'from_number': from_number,
                'to_number': to_number,
                'company_api_key': company_api_key,
                'agent_id': agent_id,
                'status': 'incoming',
                'created_at': datetime.utcnow()
            }
            
            # Create WebRTC stream URL
            stream_url = f"{base_url}/api/v1/webrtc/twilio-stream/{peer_id}/{company_api_key}/{agent_id}"
            status_callback_url = f"{base_url}/api/v1/twilio/call-status"
            
            # Generate TwiML response
            response = VoiceResponse()
            
            # Connect to WebRTC stream
            connect = Connect()
            connect.stream(url=stream_url)
            response.append(connect)
            
            # Add status callback
            response.status_callback = status_callback_url
            response.status_callback_method = "POST"
            
            logger.info(f"Generated TwiML for incoming call {call_sid} -> {peer_id}")
            db: Session = kwargs.get("db_session")
            if db:
                _call = _upsert_call(
                    db,
                    call_sid=call_sid,
                    direction="inbound",
                    from_number=from_number,
                    to_number=to_number,
                    status="incoming",
                    peer_id=peer_id,
                    company_api_key=company_api_key,
                    agent_id=agent_id,
                )
                db.add(CallEvent(call=_call, call_sid=call_sid, event="incoming"))
                db.commit()
            return response
            
        except Exception as e:
            logger.error(f"Failed to handle incoming call: {str(e)}")
            raise
            
    async def update_call_status(self, call_sid: str, status: str, **kwargs):
        """Update call status and metadata"""
        if call_sid in self.active_calls:
            self.active_calls[call_sid].update({
                'status': status,
                'updated_at': datetime.utcnow(),
                **kwargs
            })
            logger.info(f"Updated call {call_sid} status to {status}")
        db: Session = kwargs.get("db_session")
        if db:
            _fields = {"status": status}
            if "duration" in kwargs and kwargs["duration"]:
                try:
                    _fields["duration_seconds"] = int(kwargs["duration"])
                except: pass
            call = _upsert_call(db, call_sid, **_fields)
            db.add(CallEvent(call=call, call_sid=call_sid, event=status, payload=str(kwargs)))
            if status == "completed":
                call.end_time = func.now()
            db.commit()
            
    async def end_call(self, call_sid: str, db_session: Optional[Session] = None, **kwargs) -> bool:
        """End an active call"""
        if not self.client:
            return False
            
        try:
            call = self.client.calls(call_sid).update(status='completed')
            await self.update_call_status(call_sid, 'ended', db_session=kwargs.get("db_session"))
            logger.info(f"Ended call: {call_sid}")
            return True
        except Exception as e:
            logger.error(f"Failed to end call {call_sid}: {str(e)}")
            return False
            
    async def get_call_logs(self, limit: int = 100) -> list:
        """Get recent call logs"""
        if not self.client:
            return []
            
        try:
            calls = self.client.calls.list(limit=limit)
            return [{
                'sid': call.sid,
                'status': call.status,
                'direction': call.direction,
                'from': call.from_,
                'to': call.to,
                'duration': call.duration,
                'start_time': call.start_time,
                'end_time': call.end_time
            } for call in calls]
        except Exception as e:
            logger.error(f"Failed to get call logs: {str(e)}")
            return []
            
    async def cleanup_stale_calls(self, max_age_hours: int = 24):
        """Clean up old call records"""
        cutoff_time = datetime.utcnow().replace(hour=datetime.utcnow().hour - max_age_hours)
        stale_calls = [
            call_sid for call_sid, call_data in self.active_calls.items()
            if call_data.get('created_at', datetime.utcnow()) < cutoff_time
        ]
        
        for call_sid in stale_calls:
            del self.active_calls[call_sid]
            
        if stale_calls:
            logger.info(f"Cleaned up {len(stale_calls)} stale call records")
            
    def get_active_calls_count(self) -> int:
        """Get count of active calls"""
        return len(self.active_calls)
        
    def get_call_info(self, call_sid: str) -> Optional[Dict[str, Any]]:
        """Get information about a specific call"""
        return self.active_calls.get(call_sid)


twilio_service = TwilioVoiceService()