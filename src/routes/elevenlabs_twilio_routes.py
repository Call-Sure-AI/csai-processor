"""
ElevenLabs Twilio Integration API Routes
"""
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query, Request, Response
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field
import logging
import asyncio
import json
from datetime import datetime
import uuid
import tempfile
import os
from contextlib import asynccontextmanager

from services.voice.twilio_elevenlabs_integration import twilio_elevenlabs_integration
from services.voice.elevenlabs_service import elevenlabs_service
from config.settings import settings
from twilio.twiml.voice_response import VoiceResponse, Gather, Play

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/elevenlabs-twilio", tags=["ElevenLabs Twilio Integration"])

# Store audio files temporarily for serving to Twilio
audio_files: Dict[str, str] = {}

# Global initialization state
integration_initialized = False

# Pydantic models for request/response validation
class VoiceSettings(BaseModel):
    stability: float = Field(default=0.5, ge=0.0, le=1.0, description="Voice stability (0-1)")
    similarity_boost: float = Field(default=0.75, ge=0.0, le=1.0, description="Voice similarity boost (0-1)")
    style: float = Field(default=0.0, ge=0.0, le=1.0, description="Voice style (0-1)")
    use_speaker_boost: bool = Field(default=True, description="Enable speaker boost")

class CreateCallRequest(BaseModel):
    to_number: str = Field(..., description="Phone number to call")
    from_number: Optional[str] = Field(default=None, description="Phone number to call from")
    webhook_url: str = Field(..., description="Webhook URL for call events")
    voice_id: Optional[str] = Field(default=None, description="ElevenLabs voice ID")
    voice_settings: Optional[VoiceSettings] = Field(default=None, description="Voice generation settings")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Additional call metadata")

class IncomingCallRequest(BaseModel):
    call_sid: str = Field(..., description="Twilio call SID")
    from_number: str = Field(..., description="Caller phone number")
    to_number: str = Field(..., description="Called phone number")
    company_api_key: str = Field(..., description="Company API key")
    agent_id: str = Field(..., description="Agent ID")
    voice_id: Optional[str] = Field(default=None, description="ElevenLabs voice ID")
    voice_settings: Optional[VoiceSettings] = Field(default=None, description="Voice generation settings")

class SynthesizeRequest(BaseModel):
    text: str = Field(..., description="Text to synthesize")
    voice_id: Optional[str] = Field(default=None, description="ElevenLabs voice ID")
    voice_settings: Optional[VoiceSettings] = Field(default=None, description="Voice generation settings")

class StreamTextRequest(BaseModel):
    text: str = Field(..., description="Text to stream and synthesize")

class CallResponse(BaseModel):
    call_sid: str
    status: str
    direction: str
    to: str
    from_: str
    created_at: datetime
    voice_id: Optional[str] = None
    voice_settings: Optional[Dict[str, Any]] = None

class VoiceInfo(BaseModel):
    voice_id: str
    name: str
    category: str
    description: Optional[str] = None
    labels: Optional[Dict[str, str]] = None

class IntegrationInfo(BaseModel):
    call_sid: str
    voice_id: str
    voice_settings: Dict[str, Any]
    status: str
    created_at: datetime
    audio_queue_size: int
    elevenlabs_voice_info: Dict[str, Any]

# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def elevenlabs_integration_lifespan(app):
    """Lifespan context manager for ElevenLabs integration startup and shutdown"""
    global integration_initialized
    
    # Startup
    logger.info("Initializing ElevenLabs Twilio integration...")
    try:
        success = await twilio_elevenlabs_integration.initialize()
        if success:
            integration_initialized = True
            logger.info("ElevenLabs Twilio integration initialized successfully")
        else:
            logger.error("Failed to initialize ElevenLabs Twilio integration")
            integration_initialized = False
    except Exception as e:
        logger.error(f"Error during integration startup: {str(e)}")
        integration_initialized = False
    
    yield
    
    # Shutdown
    logger.info("Shutting down ElevenLabs Twilio integration...")
    try:
        await twilio_elevenlabs_integration.cleanup()
        logger.info("ElevenLabs Twilio integration shutdown complete")
    except Exception as e:
        logger.error(f"Error during integration shutdown: {str(e)}")
    finally:
        integration_initialized = False

# Dependency to check if ElevenLabs is configured and initialized
async def check_elevenlabs_config():
    """Check ElevenLabs configuration and initialization"""
    if not settings.eleven_labs_api_key:
        raise HTTPException(
            status_code=503,
            detail="ElevenLabs API key not configured. Please set ELEVEN_LABS_API_KEY environment variable."
        )
    
    # Ensure integration is initialized
    global integration_initialized
    if not integration_initialized:
        logger.warning("Integration not initialized, attempting to initialize now...")
        try:
            success = await twilio_elevenlabs_integration.initialize()
            if success:
                integration_initialized = True
                logger.info("Late initialization successful")
            else:
                raise HTTPException(
                    status_code=503,
                    detail="ElevenLabs integration not initialized and failed to initialize"
                )
        except Exception as e:
            logger.error(f"Late initialization failed: {str(e)}")
            raise HTTPException(
                status_code=503,
                detail=f"ElevenLabs integration initialization failed: {str(e)}"
            )

@router.get("/audio/{audio_id}", summary="Serve Audio File")
async def serve_audio(audio_id: str):
    """Serve audio file to Twilio"""
    # First check if it's in the routes audio_files
    if audio_id in audio_files:
        audio_path = audio_files[audio_id]
        if os.path.exists(audio_path):
            return FileResponse(
                path=audio_path,
                media_type="audio/mpeg",
                filename=f"audio_{audio_id}.mp3"
            )
        else:
            # Clean up the reference if file doesn't exist
            del audio_files[audio_id]
    
    # Check if it's in the integration's temp audio data
    if hasattr(twilio_elevenlabs_integration, 'temp_audio_data') and audio_id in twilio_elevenlabs_integration.temp_audio_data:
        audio_data = twilio_elevenlabs_integration.temp_audio_data[audio_id]
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_file:
            temp_file.write(audio_data)
            temp_path = temp_file.name
        
        # Store in audio_files for future requests
        audio_files[audio_id] = temp_path
        
        # Clean up from temp_audio_data
        del twilio_elevenlabs_integration.temp_audio_data[audio_id]
        
        return FileResponse(
            path=temp_path,
            media_type="audio/mpeg",
            filename=f"audio_{audio_id}.mp3"
        )
    
    raise HTTPException(status_code=404, detail="Audio file not found")

@router.get("/health", summary="Health Check")
async def health_check():
    """Check the health of the ElevenLabs Twilio integration"""
    try:
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow(),
            "integration_initialized": integration_initialized,
            "active_integrations": twilio_elevenlabs_integration.get_active_integrations_count() if integration_initialized else 0,
            "elevenlabs_configured": bool(settings.eleven_labs_api_key),
            "twilio_configured": bool(settings.twilio_account_sid and settings.twilio_auth_token)
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        raise HTTPException(status_code=503, detail="Service unhealthy")

@router.get("/voices", response_model=List[VoiceInfo], summary="Get Available Voices")
async def get_available_voices(
    _: None = Depends(check_elevenlabs_config)
):
    """Get list of available ElevenLabs voices"""
    try:
        voices = await twilio_elevenlabs_integration.get_available_voices()
        return [
            VoiceInfo(
                voice_id=voice.get("voice_id", ""),
                name=voice.get("name", ""),
                category=voice.get("category", ""),
                description=voice.get("description"),
                labels=voice.get("labels")
            )
            for voice in voices
        ]
    except Exception as e:
        logger.error(f"Failed to get voices: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve voices")

@router.post("/calls", response_model=CallResponse, summary="Create Integrated Call")
async def create_integrated_call(
    request: CreateCallRequest,
    _: None = Depends(check_elevenlabs_config)
):
    """Create a Twilio call with ElevenLabs voice integration"""
    try:
        # Validate voice ID if provided (with timeout and error handling)
        if request.voice_id:
            try:
                logger.info(f"Validating voice ID: {request.voice_id}")
                is_valid = await asyncio.wait_for(
                    twilio_elevenlabs_integration.validate_voice_id(request.voice_id),
                    timeout=10.0
                )
                if not is_valid:
                    logger.warning(f"Voice ID validation failed: {request.voice_id}")
                    logger.warning("Proceeding with call despite voice ID validation failure")
            except asyncio.TimeoutError:
                logger.warning(f"Voice ID validation timed out for: {request.voice_id}")
            except Exception as e:
                logger.warning(f"Voice ID validation error: {str(e)}")
        
        # Convert voice settings to dict if provided
        voice_settings_dict = None
        if request.voice_settings:
            voice_settings_dict = request.voice_settings.dict()
        
        # Create the integrated call
        call_data = await twilio_elevenlabs_integration.create_integrated_call(
            to_number=request.to_number,
            from_number=request.from_number or settings.twilio_phone_number,
            webhook_url=request.webhook_url,
            voice_id=request.voice_id,
            voice_settings=voice_settings_dict
        )
        
        # Get integration info (with error handling)
        integration_info = None
        try:
            integration_info = await twilio_elevenlabs_integration.get_integration_info(call_data['call_sid'])
        except Exception as e:
            logger.warning(f"Failed to get integration info: {str(e)}")
        
        return CallResponse(
            call_sid=call_data['call_sid'],
            status=call_data['status'],
            direction=call_data['direction'],
            to=call_data['to'],
            from_=call_data['from'],
            created_at=call_data['created_at'],
            voice_id=integration_info.get('voice_id') if integration_info else request.voice_id,
            voice_settings=integration_info.get('voice_settings') if integration_info else voice_settings_dict
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create integrated call: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create call")

@router.post("/calls/incoming", summary="Handle Incoming Integrated Call")
async def handle_incoming_integrated_call(
    request: IncomingCallRequest,
    _: None = Depends(check_elevenlabs_config)
):
    """Handle an incoming call with ElevenLabs voice integration"""
    try:
        # Validate voice ID if provided
        if request.voice_id:
            is_valid = await twilio_elevenlabs_integration.validate_voice_id(request.voice_id)
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"Invalid voice ID: {request.voice_id}")
        
        # Convert voice settings to dict if provided
        voice_settings_dict = None
        if request.voice_settings:
            voice_settings_dict = request.voice_settings.dict()
        
        # Handle the incoming call
        twiml_response = await twilio_elevenlabs_integration.handle_incoming_integrated_call(
            call_sid=request.call_sid,
            from_number=request.from_number,
            to_number=request.to_number,
            company_api_key=request.company_api_key,
            agent_id=request.agent_id,
            base_url=settings.base_url,
            voice_id=request.voice_id,
            voice_settings=voice_settings_dict
        )
        
        return {
            "call_sid": request.call_sid,
            "twiml": str(twiml_response),
            "status": "handled"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to handle incoming call: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to handle incoming call")

@router.post("/calls/{call_sid}/synthesize", summary="Synthesize Text for Call")
async def synthesize_text_for_call(
    call_sid: str,
    request: SynthesizeRequest,
    _: None = Depends(check_elevenlabs_config)
):
    """Synthesize text to audio for a specific call"""
    try:
        # Validate voice ID if provided
        if request.voice_id:
            is_valid = await twilio_elevenlabs_integration.validate_voice_id(request.voice_id)
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"Invalid voice ID: {request.voice_id}")
        
        # Convert voice settings to dict if provided
        voice_settings_dict = None
        if request.voice_settings:
            voice_settings_dict = request.voice_settings.dict()
        
        # Update call voice settings if provided
        if request.voice_id or voice_settings_dict:
            await twilio_elevenlabs_integration.update_call_voice(
                call_sid=call_sid,
                voice_id=request.voice_id,
                voice_settings=voice_settings_dict
            )
        
        # Synthesize text
        audio_data = await twilio_elevenlabs_integration.synthesize_text_for_call(
            call_sid=call_sid,
            text=request.text
        )
        
        if not audio_data:
            raise HTTPException(status_code=500, detail="Failed to synthesize text")
        
        # Return audio data as streaming response
        return StreamingResponse(
            iter([audio_data]),
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": f"attachment; filename=synthesized_{call_sid}.mp3"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to synthesize text for call {call_sid}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to synthesize text")

@router.post("/calls/{call_sid}/stream", summary="Stream Text to Call")
async def stream_text_to_call(
    call_sid: str,
    request: StreamTextRequest,
    _: None = Depends(check_elevenlabs_config)
):
    """Stream text to ElevenLabs for real-time synthesis in a call"""
    try:
        logger.info(f"Streaming text for call {call_sid}: {request.text}")
        
        # Check if integration service is properly initialized
        if not hasattr(twilio_elevenlabs_integration, 'active_integrations'):
            logger.error("Integration service not properly initialized")
            raise HTTPException(status_code=503, detail="Integration service not properly initialized")
        
        # Check if call exists in active integrations
        if call_sid not in twilio_elevenlabs_integration.active_integrations:
            logger.warning(f"Call {call_sid} not found in active integrations")
            # List available calls for debugging
            active_calls = list(twilio_elevenlabs_integration.active_integrations.keys())
            logger.info(f"Active calls: {active_calls}")
            raise HTTPException(status_code=404, detail=f"Call {call_sid} not found in active integrations. Active calls: {active_calls}")
        
        # Attempt to stream text
        success = await twilio_elevenlabs_integration.synthesize_and_stream(
            call_sid=call_sid,
            text=request.text
        )
        
        if not success:
            logger.error(f"synthesize_and_stream returned False for call {call_sid}")
            # Get more detailed error information
            integration_info = await twilio_elevenlabs_integration.get_integration_info(call_sid)
            logger.error(f"Integration info: {integration_info}")
            raise HTTPException(status_code=500, detail="Failed to stream text - check logs for details")
        
        logger.info(f"Successfully streamed text for call {call_sid}")
        return {
            "call_sid": call_sid,
            "status": "streaming",
            "text_length": len(request.text),
            "message": "Text streaming initiated successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to stream text for call {call_sid}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to stream text: {str(e)}")

@router.post("/calls/{call_sid}/stop", summary="Stop Call Synthesis")
async def stop_call_synthesis(
    call_sid: str,
    _: None = Depends(check_elevenlabs_config)
):
    """Stop current synthesis for a specific call"""
    try:
        success = await twilio_elevenlabs_integration.stop_call_synthesis(call_sid)
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to stop synthesis")
        
        return {
            "call_sid": call_sid,
            "status": "stopped"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to stop synthesis for call {call_sid}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to stop synthesis")

@router.get("/calls/{call_sid}", response_model=IntegrationInfo, summary="Get Call Integration Info")
async def get_call_integration_info(
    call_sid: str,
    _: None = Depends(check_elevenlabs_config)
):
    """Get integration information for a specific call"""
    try:
        info = await twilio_elevenlabs_integration.get_integration_info(call_sid)
        
        if not info:
            raise HTTPException(status_code=404, detail="Call not found")
        
        return IntegrationInfo(**info)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get integration info for call {call_sid}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get call info")

@router.get("/calls", response_model=List[IntegrationInfo], summary="Get All Active Integrations")
async def get_all_integrations(
    _: None = Depends(check_elevenlabs_config)
):
    """Get information about all active integrations"""
    try:
        integrations = await twilio_elevenlabs_integration.get_all_integrations()
        return [IntegrationInfo(**integration) for integration in integrations]
        
    except Exception as e:
        logger.error(f"Failed to get all integrations: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get integrations")

@router.delete("/calls/{call_sid}", summary="End Integrated Call")
async def end_integrated_call(
    call_sid: str,
    _: None = Depends(check_elevenlabs_config)
):
    """End an integrated call and cleanup resources"""
    try:
        success = await twilio_elevenlabs_integration.end_integrated_call(call_sid)
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to end call")
        
        return {
            "call_sid": call_sid,
            "status": "ended"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to end call {call_sid}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to end call")

@router.post("/calls/{call_sid}/voice", summary="Update Call Voice Settings")
async def update_call_voice(
    call_sid: str,
    voice_id: Optional[str] = Query(None, description="New voice ID"),
    voice_settings: Optional[VoiceSettings] = None,
    _: None = Depends(check_elevenlabs_config)
):
    """Update voice settings for a specific call"""
    try:
        # Validate voice ID if provided
        if voice_id:
            is_valid = await twilio_elevenlabs_integration.validate_voice_id(voice_id)
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"Invalid voice ID: {voice_id}")
        
        # Convert voice settings to dict if provided
        voice_settings_dict = None
        if voice_settings:
            voice_settings_dict = voice_settings.dict()
        
        success = await twilio_elevenlabs_integration.update_call_voice(
            call_sid=call_sid,
            voice_id=voice_id,
            voice_settings=voice_settings_dict
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update voice settings")
        
        return {
            "call_sid": call_sid,
            "status": "updated",
            "voice_id": voice_id,
            "voice_settings": voice_settings_dict
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update voice for call {call_sid}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to update voice settings")

@router.get("/voices/{voice_id}/validate", summary="Validate Voice ID")
async def validate_voice_id(
    voice_id: str,
    _: None = Depends(check_elevenlabs_config)
):
    """Validate if a voice ID exists"""
    try:
        is_valid = await twilio_elevenlabs_integration.validate_voice_id(voice_id)
        
        return {
            "voice_id": voice_id,
            "valid": is_valid
        }
        
    except Exception as e:
        logger.error(f"Failed to validate voice ID {voice_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to validate voice ID")

@router.post("/webhook", summary="ElevenLabs Call Webhook")
async def elevenlabs_call_webhook(request: Request):
    """Handle incoming calls for ElevenLabs integration - returns TwiML"""
    try:
        # Log detailed request information
        logger.info("=== ELEVENLABS WEBHOOK CALLED ===")
        logger.info(f"Request URL: {request.url}")
        logger.info(f"Request method: {request.method}")
        logger.info(f"Request headers: {dict(request.headers)}")
        
        # Check initialization first
        global integration_initialized
        if not integration_initialized:
            logger.error("Integration not initialized when webhook called")
            try:
                success = await twilio_elevenlabs_integration.initialize()
                if success:
                    integration_initialized = True
                    logger.info("Emergency initialization successful")
                else:
                    raise ValueError("Emergency initialization failed")
            except Exception as init_error:
                logger.error(f"Emergency initialization failed: {str(init_error)}")
                from twilio.twiml.voice_response import VoiceResponse
                response = VoiceResponse()
                response.say("Service initialization error. Please try again later.", voice="alice", language="en-US")
                return Response(content=str(response), media_type="application/xml")
        
        # Extract form data from Twilio
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        logger.info(f"Form data: {dict(form_data)}")
        logger.info(f"Query params: {dict(request.query_params)}")
        logger.info(f"ElevenLabs webhook called for call {call_sid} from {from_number}")
        
        # Validate required form data
        if not call_sid:
            logger.error("Missing CallSid in webhook request")
            from twilio.twiml.voice_response import VoiceResponse
            response = VoiceResponse()
            response.say("Invalid request - missing call identifier.", voice="alice", language="en-US")
            return Response(content=str(response), media_type="application/xml")
        
        # Get query parameters for customization
        voice_id = request.query_params.get("voice_id", settings.voice_id)
        company_api_key = "default"
        agent_id = "default"
        
        # Validate ElevenLabs configuration
        if not settings.eleven_labs_api_key:
            logger.error("ElevenLabs API key not configured")
            from twilio.twiml.voice_response import VoiceResponse
            response = VoiceResponse()
            response.say("Voice service not configured. Please contact support.", voice="alice", language="en-US")
            return Response(content=str(response), media_type="application/xml")
        
        # Create WebRTC stream URL with ElevenLabs integration
        base_url = settings.base_url or settings.webhook_base_url
        if not base_url:
            logger.error("Base URL not configured")
            from twilio.twiml.voice_response import VoiceResponse
            response = VoiceResponse()
            response.say("Service configuration error. Please contact support.", voice="alice", language="en-US")
            return Response(content=str(response), media_type="application/xml")
        
        # Use the direct Twilio stream endpoint for better compatibility
        stream_url = f"{base_url}/api/v1/webrtc/twilio-direct-stream/{call_sid}"
        
        logger.info(f"Creating ElevenLabs WebRTC stream: {stream_url}")
        
        # Try to handle the incoming call with ElevenLabs integration
        try:
            logger.info("Attempting ElevenLabs WebRTC integration")
            twiml_response = await twilio_elevenlabs_integration.handle_incoming_integrated_call(
                call_sid=call_sid,
                from_number=from_number,
                to_number=to_number,
                company_api_key=company_api_key,
                agent_id=agent_id,
                base_url=base_url,
                voice_id=voice_id,
                voice_settings=None
            )
            
            logger.info(f"Generated ElevenLabs TwiML for call {call_sid} with WebRTC stream")
            return Response(content=str(twiml_response), media_type="application/xml")
            
        except Exception as integration_error:
            logger.error(f"ElevenLabs integration failed: {str(integration_error)}", exc_info=True)
            logger.info("Falling back to simple TwiML response")
            
            # Fallback to simple TwiML response
            from twilio.twiml.voice_response import VoiceResponse
            response = VoiceResponse()
            response.say("Hello! This is a test call. The integration service is experiencing issues but the webhook is working correctly.", voice="alice", language="en-US")
            
            logger.info(f"Generated fallback TwiML for call {call_sid}")
            return Response(content=str(response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Critical error in ElevenLabs webhook: {str(e)}", exc_info=True)
        from twilio.twiml.voice_response import VoiceResponse
        response = VoiceResponse()
        response.say("A service error has occurred. Please try again later.", voice="alice", language="en-US")
        return Response(content=str(response), media_type="application/xml")

@router.get("/test", summary="Test ElevenLabs Configuration")
async def test_elevenlabs_config():
    """Simple test endpoint to verify ElevenLabs configuration"""
    try:
        return {
            "status": "ok",
            "message": "ElevenLabs test endpoint is working",
            "integration_initialized": integration_initialized,
            "elevenlabs_api_key_configured": bool(settings.eleven_labs_api_key),
            "voice_id_configured": bool(settings.voice_id),
            "base_url_configured": bool(settings.base_url),
            "webhook_base_url_configured": bool(getattr(settings, 'webhook_base_url', None))
        }
    except Exception as e:
        logger.error(f"Error in test endpoint: {str(e)}")
        return {
            "status": "error",
            "error": str(e)
        }

@router.post("/webhook/simple", summary="Simple ElevenLabs Webhook")
async def simple_elevenlabs_webhook(request: Request):
    """Ultra-simple webhook for testing"""
    try:
        logger.info("Simple webhook called")
        
        from twilio.twiml.voice_response import VoiceResponse
        response = VoiceResponse()
        response.say("Simple webhook test successful!", voice="alice", language="en-US")
        
        return Response(content=str(response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error in simple webhook: {str(e)}", exc_info=True)
        from twilio.twiml.voice_response import VoiceResponse
        response = VoiceResponse()
        response.say("Simple webhook error occurred.", voice="alice", language="en-US")
        return Response(content=str(response), media_type="application/xml")

@router.post("/webhook/debug", summary="Debug ElevenLabs Webhook")
async def debug_elevenlabs_webhook(request: Request):
    """Debug webhook to see what Twilio is sending"""
    try:
        form_data = await request.form()
        
        logger.info("=== DEBUG WEBHOOK CALLED ===")
        logger.info(f"Request URL: {request.url}")
        logger.info(f"Request method: {request.method}")
        logger.info(f"Request headers: {dict(request.headers)}")
        logger.info(f"Form data: {dict(form_data)}")
        logger.info(f"Query params: {dict(request.query_params)}")
        
        from twilio.twiml.voice_response import VoiceResponse
        response = VoiceResponse()
        response.say("Debug webhook working! Check the logs for details.", voice="alice", language="en-US")
        
        logger.info("=== DEBUG WEBHOOK RESPONSE SENT ===")
        return Response(content=str(response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error in debug webhook: {str(e)}", exc_info=True)
        from twilio.twiml.voice_response import VoiceResponse
        response = VoiceResponse()
        response.say("Debug webhook error occurred.", voice="alice", language="en-US")
        return Response(content=str(response), media_type="application/xml")

@router.post("/calls/{call_sid}/test-synthesis", summary="Test Text Synthesis")
async def test_text_synthesis(
    call_sid: str,
    text: str = "Hello! This is a test of the ElevenLabs text-to-speech synthesis.",
    _: None = Depends(check_elevenlabs_config)
):
    """Test text-to-speech synthesis for a specific call"""
    try:
        logger.info(f"Testing synthesis for call {call_sid} with text: {text}")
        
        # Check if call exists in active integrations
        if not hasattr(twilio_elevenlabs_integration, 'active_integrations'):
            raise HTTPException(status_code=503, detail="Integration service not properly initialized")
        
        if call_sid not in twilio_elevenlabs_integration.active_integrations:
            raise HTTPException(status_code=404, detail=f"Call {call_sid} not found in active integrations")
        
        # Use the existing synthesize method
        audio_data = await twilio_elevenlabs_integration.synthesize_text_for_call(
            call_sid=call_sid,
            text=text
        )
        
        if not audio_data:
            logger.error(f"Failed to test synthesis for call {call_sid}")
            raise HTTPException(status_code=500, detail="Failed to test synthesis")
        
        logger.info(f"Successfully tested synthesis for call {call_sid}")
        return {
            "call_sid": call_sid,
            "status": "test_completed",
            "text": text,
            "message": "Text synthesis test completed successfully",
            "audio_length": len(audio_data) if audio_data else 0
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to test synthesis for call {call_sid}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to test synthesis")

@router.post("/calls/{call_sid}/speech", summary="Handle Speech Recognition")
async def handle_speech_recognition(
    call_sid: str,
    request: Request,
    _: None = Depends(check_elevenlabs_config)
):
    """Handle speech recognition results from Twilio and generate LLM response"""
    try:
        # Get form data from Twilio
        form_data = await request.form()
        speech_result = form_data.get('SpeechResult', '').strip()
        confidence = form_data.get('Confidence', '0')
        
        logger.info(f"Received speech for call {call_sid}: '{speech_result}' (confidence: {confidence})")
        
        if not speech_result:
            # No speech detected, ask again using ElevenLabs voice
            response = VoiceResponse()
            
            no_speech_text = "I didn't hear anything. Please speak clearly."
            no_speech_audio_url = await _save_audio_and_get_url(call_sid, no_speech_text)
            
            if no_speech_audio_url:
                response.play(no_speech_audio_url)
                logger.info(f"Playing ElevenLabs no-speech audio for call {call_sid}: {no_speech_audio_url}")
            else:
                response.say(no_speech_text, voice="alice", language="en-US")
                logger.warning(f"Using Twilio fallback voice for no-speech in call {call_sid}")
            
            response.redirect(f"/api/v1/elevenlabs-twilio/calls/{call_sid}/speech")
            return Response(content=str(response), media_type="application/xml")
        
        # Process speech with LLM
        llm_response = await _process_speech_with_llm(call_sid, speech_result)
        
        # Generate TwiML response with ElevenLabs audio
        response = VoiceResponse()
        
        # Try to get ElevenLabs audio URL
        audio_url = await _save_audio_and_get_url(call_sid, llm_response)
        
        if audio_url:
            # Play ElevenLabs audio
            response.play(audio_url)
            logger.info(f"Playing ElevenLabs audio for call {call_sid}: {audio_url}")
        else:
            # Fallback to Twilio's voice
            response.say(llm_response, voice="alice", language="en-US")
            logger.warning(f"Using Twilio fallback voice for call {call_sid}")
        
        # Ask for next input using ElevenLabs voice
        follow_up_text = "Is there anything else I can help you with?"
        follow_up_audio_url = await _save_audio_and_get_url(call_sid, follow_up_text)
        
        gather = Gather(
            input='speech',
            action=f"/api/v1/elevenlabs-twilio/calls/{call_sid}/speech",
            method='POST',
            speech_timeout='auto',
            language='en-US',
            enhanced='true'
        )
        
        if follow_up_audio_url:
            gather.play(follow_up_audio_url)
            logger.info(f"Playing ElevenLabs follow-up audio for call {call_sid}: {follow_up_audio_url}")
        else:
            gather.say(follow_up_text, voice="alice", language="en-US")
            logger.warning(f"Using Twilio fallback voice for follow-up in call {call_sid}")
        
        response.append(gather)
        
        # Fallback using ElevenLabs voice
        goodbye_text = "Thank you for calling. Have a great day!"
        goodbye_audio_url = await _save_audio_and_get_url(call_sid, goodbye_text)
        
        if goodbye_audio_url:
            response.play(goodbye_audio_url)
            logger.info(f"Playing ElevenLabs goodbye audio for call {call_sid}: {goodbye_audio_url}")
        else:
            response.say(goodbye_text, voice="alice", language="en-US")
            logger.warning(f"Using Twilio fallback voice for goodbye in call {call_sid}")
        
        response.hangup()
        
        logger.info(f"Generated response for call {call_sid}: '{llm_response}'")
        return Response(content=str(response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error handling speech for call {call_sid}: {str(e)}")
        response = VoiceResponse()
        response.say("I'm sorry, I'm having trouble processing your request. Please try again.", voice="alice", language="en-US")
        return Response(content=str(response), media_type="application/xml")

async def _process_speech_with_llm(call_sid: str, user_speech: str) -> str:
    """Process user speech with LLM and return response"""
    try:
        from services.llm_service import MultiProviderLLMService
        from services.speech.conversation_manager_service import ConversationManager
        
        # Get or create conversation manager for this call
        if not hasattr(_process_speech_with_llm, 'conversation_managers'):
            _process_speech_with_llm.conversation_managers = {}
        
        if call_sid not in _process_speech_with_llm.conversation_managers:
            agent_config = {
                "name": "Customer Service Agent",
                "personality": "friendly and helpful",
                "context": "customer service call"
            }
            _process_speech_with_llm.conversation_managers[call_sid] = ConversationManager(agent_config)
            # Initialize the conversation
            await _process_speech_with_llm.conversation_managers[call_sid].start_call(call_sid)
        
        conversation_manager = _process_speech_with_llm.conversation_managers[call_sid]
        
        # Process user input and get LLM response
        llm_response = await conversation_manager.process_user_input(
            call_sid=call_sid,
            user_input=user_speech
        )
        
        logger.info(f"Generated LLM response for call {call_sid}: '{llm_response}'")
        return llm_response
        
    except Exception as e:
        logger.error(f"Error processing speech with LLM for call {call_sid}: {str(e)}")
        return "I'm sorry, I'm having trouble processing your request right now. Could you please try again?"

async def _save_audio_and_get_url(call_sid: str, text: str) -> Optional[str]:
    """Synthesize text with ElevenLabs and save to file, return URL"""
    try:
        # Get the voice ID from the active integration
        if call_sid in twilio_elevenlabs_integration.active_integrations:
            integration_info = twilio_elevenlabs_integration.active_integrations[call_sid]
            voice_id = integration_info.get('voice_id', 'default')
            logger.info(f"Using ElevenLabs voice ID: {voice_id} for call {call_sid}")
        else:
            voice_id = 'default'
            logger.warning(f"Call {call_sid} not found in active integrations, using default voice")
        
        # Synthesize text with ElevenLabs
        audio_data = await twilio_elevenlabs_integration.synthesize_text_for_call(
            call_sid=call_sid,
            text=text
        )
        
        if audio_data:
            # Generate unique audio ID
            audio_id = f"{call_sid}_{uuid.uuid4().hex[:8]}"
            
            # Save audio to temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_file:
                temp_file.write(audio_data)
                temp_path = temp_file.name
            
            # Store the path for serving
            audio_files[audio_id] = temp_path
            
            # Generate URL
            base_url = settings.base_url or settings.webhook_base_url
            audio_url = f"{base_url}/api/v1/elevenlabs-twilio/audio/{audio_id}"
            
            logger.info(f"Saved audio file for call {call_sid} with voice {voice_id}: {audio_url}")
            return audio_url
        else:
            logger.error(f"Failed to synthesize audio for call {call_sid}")
            return None
            
    except Exception as e:
        logger.error(f"Error saving audio for call {call_sid}: {str(e)}")
        return None

async def _synthesize_and_stream_elevenlabs(call_sid: str, text: str):
    """Synthesize text with ElevenLabs and stream to the call"""
    try:
        # Get the voice ID from the active integration
        if call_sid in twilio_elevenlabs_integration.active_integrations:
            integration_info = twilio_elevenlabs_integration.active_integrations[call_sid]
            voice_id = integration_info.get('voice_id', 'default')
            logger.info(f"Using ElevenLabs voice ID: {voice_id} for call {call_sid}")
        else:
            voice_id = 'default'
            logger.warning(f"Call {call_sid} not found in active integrations, using default voice")
        
        # Synthesize and stream text with ElevenLabs
        success = await twilio_elevenlabs_integration.synthesize_and_stream(
            call_sid=call_sid,
            text=text
        )
        
        if success:
            logger.info(f"Successfully synthesized and streamed with ElevenLabs voice {voice_id}")
        else:
            logger.error(f"Failed to synthesize and stream for call {call_sid}")
            
    except Exception as e:
        logger.error(f"Error synthesizing and streaming ElevenLabs response for call {call_sid}: {str(e)}")