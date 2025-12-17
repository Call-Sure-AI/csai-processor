from fastapi import APIRouter, Request, WebSocket, Query
from fastapi.responses import Response
from services.telephony import get_telephony_provider
from config.settings import settings
import logging

# Import your existing working handlers directly
from routes.twilio_elevenlabs_routes import (
    handle_incoming_call_elevenlabs,
    handle_media_stream,
    handle_call_status,
    initiate_outbound_call
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/calls", tags=["unified-calls"])


@router.post("/incoming")
async def handle_incoming_call_unified(
    request: Request,
    provider: str = Query(default="twilio")
):
    """
    Unified incoming call handler
    Currently supports: Twilio, Exotel (future)
    """
    logger.info(f"📞 Incoming call with provider: {provider}")
    
    if provider == "twilio":
        # Delegate to your existing working Twilio handler
        return await handle_incoming_call_elevenlabs(request)
    
    elif provider == "exotel":
        # TODO: Implement Exotel handler when ready
        logger.warning("Exotel not yet implemented")
        error_response = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">Exotel integration coming soon.</Say>
</Response>"""
        return Response(content=error_response, media_type="application/xml")
    
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
    Currently supports: Twilio, Exotel (future)
    """
    logger.info(f"🔌 WebSocket with provider: {provider}")
    
    if provider == "twilio":
        # Delegate to your existing working Twilio WebSocket handler
        return await handle_media_stream(websocket)
    
    elif provider == "exotel":
        # TODO: Implement Exotel WebSocket handler when ready
        logger.warning("Exotel WebSocket not yet implemented")
        await websocket.accept()
        await websocket.send_json({"error": "Exotel integration coming soon"})
        await websocket.close()
    
    else:
        logger.error(f"Unknown provider: {provider}")
        await websocket.accept()
        await websocket.close(code=1008, reason="Invalid provider")


@router.post("/status")
async def handle_call_status_unified(request: Request):
    """
    Unified status callback - works for both Twilio and Exotel
    """
    # Use your existing working status handler
    return await handle_call_status(request)


@router.post("/outbound")
async def initiate_outbound_call_unified(
    request: Request,
    provider: str = Query(default="twilio")
):
    """
    Unified outbound call initiation
    """
    try:
        data = await request.json()
        
        logger.info(f"📞 Outbound call via {provider}")
        logger.info(f"   From: {data.get('from_number')} → To: {data.get('to_number')}")
        
        # Get provider instance
        telephony = get_telephony_provider(provider)
        
        # Initiate call via provider API
        result = await telephony.initiate_call(
            from_number=data["from_number"],
            to_number=data["to_number"],
            webhook_url=data["webhook_url"],
            status_callback_url=data.get("status_callback_url"),
            record=data.get("record", False),
            timeout=data.get("timeout", 60)
        )
        
        logger.info(f"✅ Call initiated: {result.get('call_sid')}")
        return result
        
    except KeyError as e:
        logger.error(f"Missing required field: {e}")
        return {"error": f"Missing required field: {str(e)}"}, 400
    except Exception as e:
        logger.error(f"Outbound call failed: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e)}, 500
