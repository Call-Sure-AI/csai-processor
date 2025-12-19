# src/routes/call_routes.py

from fastapi import APIRouter, Request, WebSocket, Query
from fastapi.responses import Response
from services.telephony import get_telephony_provider
from config.settings import settings
import logging

from routes.twilio_elevenlabs_routes import (
    handle_incoming_call_elevenlabs as twilio_incoming_handler,
    handle_media_stream as twilio_media_stream_handler,
    handle_call_status as twilio_status_handler,
    initiate_outbound_call as twilio_outbound_handler
)

from routes.exotel_elevenlabs_routes import (
    handle_incoming_call_exotel as exotel_incoming_handler,
    handle_media_stream_exotel as exotel_media_stream_handler,
    handle_call_status_exotel as exotel_status_handler,
    initiate_outbound_call as exotel_outbound_handler
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/incoming")
async def handle_incoming_call_unified(
    request: Request,
    provider: str = Query(default="twilio")
):
    """
    Unified incoming call handler
    Routes to Twilio or Exotel based on provider parameter
    """
    logger.info(f"Incoming call with provider: {provider}")
    
    if provider == "twilio":
        return await twilio_incoming_handler(request)
    
    elif provider == "exotel":
        return await exotel_incoming_handler(request)
    
    else:
        logger.error(f"Unknown provider: {provider}")
        error_response = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">Invalid provider configuration.</Say>
</Response>"""
        return Response(content=error_response, media_type="application/xml")


@router.websocket("/media-stream")
async def handle_media_stream_unified(
    websocket: WebSocket,
    provider: str = Query(default="twilio")
):
    """
    Unified WebSocket handler
    Routes to Twilio or Exotel based on provider parameter
    """
    logger.info(f"WebSocket with provider: {provider}")
    
    if provider == "twilio":
        return await twilio_media_stream_handler(websocket)
    
    elif provider == "exotel":
        return await exotel_media_stream_handler(websocket)
    
    else:
        logger.error(f"Unknown provider: {provider}")
        await websocket.accept()
        await websocket.close(code=1008, reason="Invalid provider")


@router.post("/status")
async def handle_call_status_unified(
    request: Request,
    provider: str = Query(default="twilio")
):
    """
    Unified status callback
    Routes to Twilio or Exotel based on provider parameter
    """
    logger.info(f"Status callback with provider: {provider}")
    
    if provider == "twilio":
        return await twilio_status_handler(request)
    
    elif provider == "exotel":
        return await exotel_status_handler(request)
    
    else:
        return {"status": "error", "message": "Invalid provider"}


@router.post("/outbound")
async def initiate_outbound_call_unified(
    request: Request,
    provider: str = Query(default="twilio")
):
    """
    Unified outbound call initiation
    Calls provider-specific initiate_outbound_call() functions directly
    """
    try:
        logger.info(f"Outbound call via {provider}")

        if provider == "twilio":
            return await twilio_outbound_handler(request)
        elif provider == "exotel":
            return await exotel_outbound_handler(request)
        else:
            logger.error(f"Unknown provider: {provider}")
            return {"error": f"Invalid provider: {provider}"}, 400
            
    except Exception as e:
        logger.error(f"Outbound call failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e)}, 500