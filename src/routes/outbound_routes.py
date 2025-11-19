from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import Response
from sqlalchemy.orm import Session
from database.config import get_db
from database.models import OutboundCall
from services.outbound_call_service import outbound_call_service
from services.agent_tools import execute_function
from twilio.twiml.voice_response import VoiceResponse, Gather
from datetime import datetime
import logging
import urllib.parse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/outbound", tags=["outbound"])

BOOKING_SCRIPT = """
I'm reaching out to schedule a consultation call to discuss how Callsure's AI voice agents can help automate your business communications and boost productivity. 

Do you have about 15 minutes available this week for a quick call?
"""

@router.post("/connect")
async def handle_outbound_connect(
    request: Request,
    db: Session = Depends(get_db)
):
    """Handle initial outbound call connection"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        
        campaign_id = request.query_params.get("campaign_id")
        agent_id = request.query_params.get("agent_id")
        company_id = request.query_params.get("company_id")
        customer_name = urllib.parse.unquote(request.query_params.get("customer_name", "there"))
        attempt = request.query_params.get("attempt", "1")
        
        logger.info(f"Outbound call connected: {call_sid}, Customer: {customer_name}")
        
        # Personalized greeting
        if customer_name and customer_name != "there":
            greeting = f"Hello {customer_name}! This is Shreni calling from Callsure. {BOOKING_SCRIPT}"
        else:
            greeting = f"Hello! This is Shreni calling from Callsure. {BOOKING_SCRIPT}"
        
        response = VoiceResponse()
        
        gather = Gather(
            input='speech dtmf',
            timeout=10,
            speech_timeout='auto',
            action=f'/api/v1/outbound/process?campaign_id={campaign_id}&agent_id={agent_id}&company_id={company_id}&customer_name={urllib.parse.quote(customer_name)}',
            method='POST',
            speech_model='phone_call',
            enhanced='true',
            language='en-US'
        )
        gather.say(greeting, voice="Polly.Joanna", language="en-US")
        response.append(gather)
        
        response.say(
            "I didn't hear a response. I'll try reaching out again later. Have a great day!",
            voice="Polly.Joanna"
        )
        
        return Response(content=str(response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error in connect: {str(e)}")
        response = VoiceResponse()
        response.say("An error occurred. Goodbye!", voice="Polly.Joanna")
        return Response(content=str(response), media_type="application/xml")

@router.post("/process")
async def handle_outbound_process(
    request: Request,
    db: Session = Depends(get_db)
):
    """Process customer response"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        speech_result = form_data.get("SpeechResult", "").lower()
        
        campaign_id = request.query_params.get("campaign_id")
        agent_id = request.query_params.get("agent_id")
        company_id = request.query_params.get("company_id")
        customer_name = urllib.parse.unquote(request.query_params.get("customer_name", ""))
        
        logger.info(f"Response: '{speech_result}'")
        
        response = VoiceResponse()
        
        positive_keywords = ["yes", "sure", "okay", "interested", "available", "tell me", "sounds good"]
        negative_keywords = ["no", "not interested", "busy", "don't call", "remove", "stop"]
        
        if any(word in speech_result for word in negative_keywords):
            response.say(
                "I completely understand. Thank you for your time. Have a wonderful day!",
                voice="Polly.Joanna"
            )
            
            call = db.query(OutboundCall).filter(OutboundCall.call_sid == call_sid).first()
            if call:
                call.status = "rejected"
                call.notes = speech_result
                call.ended_at = datetime.utcnow()
                db.commit()
            
            response.hangup()
            
        elif any(word in speech_result for word in positive_keywords):
            gather = Gather(
                input='speech dtmf',
                timeout=10,
                speech_timeout='auto',
                action=f'/api/v1/outbound/schedule?campaign_id={campaign_id}&agent_id={agent_id}&company_id={company_id}&customer_name={urllib.parse.quote(customer_name)}',
                method='POST',
                speech_model='phone_call',
                enhanced='true'
            )
            gather.say(
                "Wonderful! I have availability tomorrow and the day after. What time works best for you - morning or afternoon?",
                voice="Polly.Joanna"
            )
            response.append(gather)
            
            call = db.query(OutboundCall).filter(OutboundCall.call_sid == call_sid).first()
            if call:
                call.status = "interested"
                call.notes = speech_result
                db.commit()
            
        else:
            gather = Gather(
                input='speech dtmf',
                timeout=10,
                speech_timeout='auto',
                action=f'/api/v1/outbound/process?campaign_id={campaign_id}&agent_id={agent_id}&company_id={company_id}&customer_name={urllib.parse.quote(customer_name)}',
                method='POST',
                speech_model='phone_call',
                enhanced='true'
            )
            gather.say(
                "I'm sorry, I didn't quite catch that. Would you be interested in a brief 15-minute consultation call?",
                voice="Polly.Joanna"
            )
            response.append(gather)
        
        return Response(content=str(response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        response = VoiceResponse()
        response.say("Thank you for your time!", voice="Polly.Joanna")
        return Response(content=str(response), media_type="application/xml")

@router.post("/schedule")
async def handle_scheduling(
    request: Request,
    db: Session = Depends(get_db)
):
    """Handle booking"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        speech_result = form_data.get("SpeechResult", "")
        from_number = form_data.get("From")
        
        campaign_id = request.query_params.get("campaign_id")
        company_id = request.query_params.get("company_id")
        customer_name = urllib.parse.unquote(request.query_params.get("customer_name", ""))
        
        from datetime import datetime, timedelta
        
        tomorrow = datetime.now() + timedelta(days=1)
        preferred_date = tomorrow.strftime("%Y-%m-%d")
        
        if "morning" in speech_result.lower():
            preferred_time = "10:00 AM"
        elif "afternoon" in speech_result.lower():
            preferred_time = "2:00 PM"
        else:
            preferred_time = "10:00 AM"
        
        booking_response = await execute_function(
            function_name="create_booking",
            arguments={
                "customer_name": customer_name,
                "customer_phone": from_number,
                "preferred_date": preferred_date,
                "preferred_time": preferred_time,
                "notes": f"Booked via outbound call. Customer said: {speech_result}"
            },
            company_id=company_id,
            call_sid=call_sid,
            campaign_id=campaign_id
        )
        
        call = db.query(OutboundCall).filter(OutboundCall.call_sid == call_sid).first()
        if call:
            call.status = "booked"
            call.notes = speech_result
            call.ended_at = datetime.utcnow()
            db.commit()
        
        response = VoiceResponse()
        response.say(booking_response, voice="Polly.Joanna")
        response.say("Have a wonderful day! Goodbye.", voice="Polly.Joanna")
        response.hangup()
        
        return Response(content=str(response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        response = VoiceResponse()
        response.say(
            "Perfect! We'll send you a confirmation email. Thank you!",
            voice="Polly.Joanna"
        )
        response.hangup()
        return Response(content=str(response), media_type="application/xml")

@router.post("/status")
async def handle_call_status(
    request: Request,
    db: Session = Depends(get_db)
):
    """Handle call status callbacks"""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        call_status = form_data.get("CallStatus")
        call_duration = form_data.get("CallDuration")
        recording_url = form_data.get("RecordingUrl")
        
        logger.info(f"Status: {call_sid} - {call_status}")
        
        call = db.query(OutboundCall).filter(OutboundCall.call_sid == call_sid).first()
        if call:
            call.status = call_status
            if call_duration:
                call.duration = int(call_duration)
            if recording_url:
                call.recording_url = recording_url
            if call_status in ['completed', 'no-answer', 'busy', 'failed', 'canceled']:
                call.ended_at = datetime.utcnow()
            db.commit()
        
        return {"status": "ok"}
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return {"status": "error"}

@router.post("/campaigns/{campaign_id}/start")
async def start_campaign(
    campaign_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Start outbound campaign"""
    try:
        logger.info(f"Starting campaign: {campaign_id}")
        
        background_tasks.add_task(
            outbound_call_service.process_campaign,
            campaign_id,
            db
        )
        
        return {
            "success": True,
            "message": f"Campaign {campaign_id} started",
            "campaign_id": campaign_id
        }
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/campaigns/{campaign_id}/retry")
async def retry_campaign(
    campaign_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Retry failed calls"""
    try:
        background_tasks.add_task(
            outbound_call_service.retry_failed_calls,
            campaign_id,
            db
        )
        
        return {
            "success": True,
            "message": f"Retrying failed calls for {campaign_id}"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/campaigns/{campaign_id}/stats")
async def get_stats(
    campaign_id: str,
    db: Session = Depends(get_db)
):
    """Get campaign stats"""
    try:
        calls = db.query(OutboundCall).filter(
            OutboundCall.campaign_id == campaign_id
        ).all()
        
        stats = {
            "total_calls": len(calls),
            "by_status": {},
            "bookings_made": 0
        }
        
        for call in calls:
            status = call.status
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
            if call.status == "booked":
                stats["bookings_made"] += 1
        
        return {
            "success": True,
            "campaign_id": campaign_id,
            "stats": stats
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
