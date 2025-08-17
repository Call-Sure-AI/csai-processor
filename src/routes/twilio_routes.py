from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import Response
from typing import Dict, Any, Optional
import logging
import json
import asyncio
from datetime import datetime

from services.voice.twilio_service import TwilioVoiceService
from managers.connection_manager import ConnectionManager
from config.settings import settings
from database.config import get_db
from database.models import Company, Agent

router = APIRouter()
logger = logging.getLogger(__name__)

# Global service instance
twilio_service: Optional[TwilioVoiceService] = None

async def get_twilio_service() -> TwilioVoiceService:
    """Dependency to get Twilio service instance"""
    global twilio_service
    if not twilio_service:
        twilio_service = TwilioVoiceService()
        await twilio_service.initialize()
    return twilio_service

@router.post("/incoming-call")
async def handle_incoming_call(
    request: Request,
    twilio_service: TwilioVoiceService = Depends(get_twilio_service)
):
    """Handle incoming Twilio voice calls"""
    try:
        # Extract form data
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        # Validate request (optional but recommended)
        signature = request.headers.get("X-Twilio-Signature", "")
        request_url = str(request.url)
        if not twilio_service.validate_request(signature, request_url, dict(form_data)):
            logger.warning("Invalid Twilio signature")
            # In production, you might want to reject invalid requests
        
        # Get company and agent info from query params or headers
        company_api_key = request.query_params.get("company_key") or settings.default_company_api_key
        agent_id = request.query_params.get("agent_id") or settings.default_agent_id
        
        # Get base URL for WebRTC stream
        base_url = f"https://{request.headers.get('host')}"
        
        # Generate TwiML response
        twiml_response = await twilio_service.handle_incoming_call(
            call_sid=call_sid,
            from_number=from_number,
            to_number=to_number,
            company_api_key=company_api_key,
            agent_id=agent_id,
            base_url=base_url
        )
        
        logger.info(f"Handled incoming call {call_sid} from {from_number}")
        return Response(content=str(twiml_response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error handling incoming call: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/call-status")
async def handle_call_status(
    request: Request,
    twilio_service: TwilioVoiceService = Depends(get_twilio_service)
):
    """Handle Twilio call status callbacks"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        call_status = form_data.get("CallStatus")
        call_duration = form_data.get("CallDuration")
        
        # Update call status
        await twilio_service.update_call_status(
            call_sid=call_sid,
            status=call_status,
            duration=call_duration,
            timestamp=datetime.utcnow()
        )
        
        # Log call completion
        if call_status == "completed":
            logger.info(f"Call {call_sid} completed after {call_duration} seconds")
            
        return Response(content="OK", media_type="text/plain")
        
    except Exception as e:
        logger.error(f"Error handling call status: {str(e)}")
        return Response(content="Error", media_type="text/plain", status_code=500)

@router.post("/outbound-call")
async def create_outbound_call(
    request: Request,
    twilio_service: TwilioVoiceService = Depends(get_twilio_service)
):
    """Create an outbound call"""
    try:
        data = await request.json()
        to_number = data.get("to_number")
        from_number = data.get("from_number")
        company_api_key = data.get("company_api_key")
        agent_id = data.get("agent_id")
        
        if not all([to_number, company_api_key, agent_id]):
            raise HTTPException(status_code=400, detail="Missing required parameters")
        
        # Create webhook URL for call handling
        base_url = f"https://{request.headers.get('host')}"
        webhook_url = f"{base_url}/api/v1/twilio/incoming-call?company_key={company_api_key}&agent_id={agent_id}"
        status_callback_url = f"{base_url}/api/v1/twilio/call-status"
        
        # Create the call
        call_data = await twilio_service.create_call(
            to_number=to_number,
            from_number=from_number,
            webhook_url=webhook_url,
            status_callback_url=status_callback_url,
            metadata={
                'company_api_key': company_api_key,
                'agent_id': agent_id
            }
        )
        
        return {"success": True, "call": call_data}
        
    except Exception as e:
        logger.error(f"Error creating outbound call: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/calls")
async def get_call_logs(
    limit: int = 100,
    twilio_service: TwilioVoiceService = Depends(get_twilio_service)
):
    """Get recent call logs"""
    try:
        calls = await twilio_service.get_call_logs(limit=limit)
        return {"calls": calls}
    except Exception as e:
        logger.error(f"Error getting call logs: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.delete("/calls/{call_sid}")
async def end_call(
    call_sid: str,
    twilio_service: TwilioVoiceService = Depends(get_twilio_service)
):
    """End an active call"""
    try:
        success = await twilio_service.end_call(call_sid)
        if success:
            return {"success": True, "message": f"Call {call_sid} ended"}
        else:
            raise HTTPException(status_code=404, detail="Call not found or already ended")
    except Exception as e:
        logger.error(f"Error ending call {call_sid}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/active-calls")
async def get_active_calls(
    twilio_service: TwilioVoiceService = Depends(get_twilio_service)
):
    """Get currently active calls"""
    try:
        active_calls = twilio_service.active_calls
        return {
            "active_calls": active_calls,
            "count": len(active_calls)
        }
    except Exception as e:
        logger.error(f"Error getting active calls: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/calls/{call_sid}")
async def get_call_info(
    call_sid: str,
    twilio_service: TwilioVoiceService = Depends(get_twilio_service)
):
    """Get information about a specific call"""
    try:
        call_info = twilio_service.get_call_info(call_sid)
        if call_info:
            return {"call": call_info}
        else:
            raise HTTPException(status_code=404, detail="Call not found")
    except Exception as e:
        logger.error(f"Error getting call info for {call_sid}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/cleanup")
async def cleanup_stale_calls(
    twilio_service: TwilioVoiceService = Depends(get_twilio_service)
):
    """Manually trigger cleanup of stale call records"""
    try:
        await twilio_service.cleanup_stale_calls()
        return {"success": True, "message": "Cleanup completed"}
    except Exception as e:
        logger.error(f"Error during cleanup: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")
