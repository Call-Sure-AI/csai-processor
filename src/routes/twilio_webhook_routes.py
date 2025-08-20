"""
Twilio Webhook Routes for Call Handling
"""
from typing import Optional, Dict, Any
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import Response
from loguru import logger
from datetime import datetime

from config.settings import settings

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

@router.post("/incoming-call")
async def handle_incoming_call(
    request: Request,
    CallSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(...),
    CallStatus: str = Form(...),
    Direction: str = Form(...),
    message: Optional[str] = Form(None),
    voice: Optional[str] = Form("alice"),
    language: Optional[str] = Form("en-US"),
    gather_input: Optional[bool] = Form(False)
):
    """
    Handle incoming call webhook from Twilio
    
    This endpoint receives webhook calls from Twilio when a call is initiated
    and returns TwiML instructions for what the call should do.
    """
    try:
        # Log the incoming call
        logger.info(f"Incoming call from {From} to {To} (SID: {CallSid})")
        logger.info(f"Call status: {CallStatus}, Direction: {Direction}")
        
        # Get query parameters from URL
        query_params = dict(request.query_params)
        logger.info(f"Query parameters: {query_params}")
        
        # Get custom message from query params, form data, or use default
        base_message = (
            query_params.get('message') or 
            message or 
            "Hello! This is a test call from your Celery Twilio service. Thank you for testing our call queuing system!"
        )
        
        # Check if we have custom data for personalized messages
        custom_data_str = query_params.get('custom_data')
        if custom_data_str:
            try:
                import json
                custom_data = json.loads(custom_data_str)
                recipients = custom_data.get('recipients', {})
                
                # Check if we have personalized data for this number
                if From in recipients:
                    recipient_data = recipients[From]
                    message_template = custom_data.get('message_template', base_message)
                    
                    # Replace placeholders in the template
                    custom_message = message_template
                    for key, value in recipient_data.items():
                        custom_message = custom_message.replace(f"{{{key}}}", str(value))
                    
                    logger.info(f"Using personalized message for {From}: {custom_message}")
                else:
                    custom_message = base_message
            except Exception as e:
                logger.error(f"Error parsing custom data: {str(e)}")
                custom_message = base_message
        else:
            custom_message = base_message
        
        # Get voice and language from query params or form data
        custom_voice = query_params.get('voice') or voice or "alice"
        custom_language = query_params.get('language') or language or "en-US"
        custom_gather_input = query_params.get('gather_input', 'false').lower() == 'true' or gather_input
        
        logger.info(f"Using message: {custom_message}")
        logger.info(f"Using voice: {custom_voice}, language: {custom_language}, gather_input: {custom_gather_input}")
        
        # Generate TwiML response
        twiml_response = generate_twiml_response(
            message=custom_message,
            voice=custom_voice,
            language=custom_language,
            gather_input=custom_gather_input
        )
        
        logger.info(f"Generated TwiML response for call {CallSid}")
        
        # Return TwiML response
        return Response(
            content=twiml_response,
            media_type="application/xml"
        )
        
    except Exception as e:
        logger.error(f"Error handling incoming call webhook: {str(e)}")
        # Return a simple error TwiML
        error_twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice" language="en-US">Sorry, there was an error processing your call. Please try again later.</Say>
</Response>"""
        return Response(
            content=error_twiml,
            media_type="application/xml"
        )

@router.post("/call-status")
async def handle_call_status(
    request: Request,
    CallSid: str = Form(...),
    CallStatus: str = Form(...),
    From: str = Form(...),
    To: str = Form(...),
    CallDuration: Optional[str] = Form(None),
    RecordingUrl: Optional[str] = Form(None)
):
    """
    Handle call status webhook from Twilio
    
    This endpoint receives status updates from Twilio about call progress.
    """
    try:
        logger.info(f"Call status update - SID: {CallSid}, Status: {CallStatus}")
        logger.info(f"From: {From}, To: {To}, Duration: {CallDuration}")
        
        if CallStatus == "completed":
            logger.info(f"Call {CallSid} completed successfully")
        elif CallStatus == "failed":
            logger.error(f"Call {CallSid} failed")
        elif CallStatus == "busy":
            logger.warning(f"Call {CallSid} - number was busy")
        elif CallStatus == "no-answer":
            logger.warning(f"Call {CallSid} - no answer")
        
        # Return empty response (Twilio doesn't need a response for status callbacks)
        return {"status": "received"}
        
    except Exception as e:
        logger.error(f"Error handling call status webhook: {str(e)}")
        return {"status": "error", "message": str(e)}

@router.post("/gather-input")
async def handle_gather_input(
    request: Request,
    CallSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(...),
    Digits: Optional[str] = Form(None),
    SpeechResult: Optional[str] = Form(None)
):
    """
    Handle gathered input from user during call
    
    This endpoint receives the result of a <Gather> verb from TwiML.
    """
    try:
        logger.info(f"Gathered input from call {CallSid}")
        logger.info(f"Digits: {Digits}, Speech: {SpeechResult}")
        
        # Process the gathered input
        if Digits:
            response_message = f"You pressed {Digits}. Thank you for your input!"
        elif SpeechResult:
            response_message = f"You said: {SpeechResult}. Thank you for your response!"
        else:
            response_message = "No input received. Thank you for your time!"
        
        # Generate response TwiML
        twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice" language="en-US">{response_message}</Say>
    <Say voice="alice" language="en-US">Goodbye!</Say>
</Response>"""
        
        return Response(
            content=twiml_response,
            media_type="application/xml"
        )
        
    except Exception as e:
        logger.error(f"Error handling gather input: {str(e)}")
        error_twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice" language="en-US">Sorry, there was an error processing your input. Goodbye!</Say>
</Response>"""
        return Response(
            content=error_twiml,
            media_type="application/xml"
        )

@router.get("/test-call")
async def test_call_webhook():
    """
    Test endpoint to verify webhook is working
    
    Returns a simple TwiML response for testing.
    """
    test_twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice" language="en-US">This is a test call from your Twilio webhook. The webhook is working correctly!</Say>
</Response>"""
    
    return Response(
        content=test_twiml,
        media_type="application/xml"
    )
