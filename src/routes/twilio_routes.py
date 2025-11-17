from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import Response
from typing import Dict, Any, Optional
import logging
import json
import asyncio
from datetime import datetime

from services.voice.twilio_service import TwilioVoiceService
from services.llm_service import llm_service
from services.speech.conversation_manager_service import create_conversation_manager
from managers.connection_manager import ConnectionManager
from config.settings import settings
from database.config import get_db
from database.models import Company, Agent
from sqlalchemy.orm import Session
from database.config import get_db
from database.models import Call, CallEvent, ConversationTurn

router = APIRouter()
logger = logging.getLogger(__name__)

# Global service instance
twilio_service: Optional[TwilioVoiceService] = None

# Global conversation managers for active calls
conversation_managers: Dict[str, Any] = {}

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
    twilio_service: TwilioVoiceService = Depends(get_twilio_service),
    db: Session = Depends(get_db)
):
    """Handle incoming Twilio voice calls with LLM integration"""
    try:
        # Extract form data
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        from_number = form_data.get("From")
        to_number = form_data.get("To")
        
        # Get query parameters for customizing the call
        voice = request.query_params.get("voice", "alice")
        language = request.query_params.get("language", "en-US")
        gather_input = request.query_params.get("gather_input", "true").lower() == "true"  # Default to true for chat
        initial_message = request.query_params.get("message", "")
        
        # Validate request (optional but recommended)
        signature = request.headers.get("X-Twilio-Signature", "")
        request_url = str(request.url)
        
        # Convert form data to proper dictionary format for validation
        form_dict = {}
        for key, value in form_data.items():
            form_dict[key] = value
        
        try:
            if not twilio_service.validate_request(signature, request_url, form_dict):
                logger.warning("Invalid Twilio signature")
                # In production, you might want to reject invalid requests
        except Exception as e:
            logger.warning(f"Error validating Twilio signature: {str(e)}")
            # Continue with the request even if validation fails
        
        # Get company and agent info from query params or headers
        company_api_key = request.query_params.get("company_key") or settings.default_company_api_key or "default"
        agent_id = request.query_params.get("agent_id") or settings.default_agent_id or "default"
        
        # Create conversation manager for this call
        agent_config = {
            "name": f"Agent {agent_id}",
            "voice": voice,
            "language": language,
            "call_type": "support",
            "company_key": company_api_key,
            "agent_id": agent_id
        }
        
        conversation_manager = create_conversation_manager(agent_config)
        conversation_managers[call_sid] = conversation_manager
        
        # Start the conversation
        greeting = await conversation_manager.start_call(call_sid)
        
        # Use the greeting from LLM if no initial message provided
        if not initial_message:
            initial_message = greeting
        
        logger.info(f"Handling incoming call {call_sid} from {from_number} with greeting: {initial_message}")
        
        # Generate TwiML response with Gather for user input
        from twilio.twiml.voice_response import VoiceResponse, Gather
        
        response = VoiceResponse()
        
        if gather_input:
            # Create TwiML with Gather for user input
            gather = Gather(
                input='speech dtmf',
                timeout=15,  # Increased timeout
                speech_timeout='auto',  # Auto speech timeout
                action=f'/api/v1/twilio/gather-callback?company_key={company_api_key}&agent_id={agent_id}',
                method='POST',
                speech_model='phone_call',  # Optimized for phone calls
                enhanced='true'  # Enhanced speech recognition
            )
            gather.say(initial_message, voice=voice, language=language)
            response.append(gather)
            
            # Fallback if no input is received
            response.say("I didn't hear anything. Please try again or say goodbye to end the call.", voice=voice, language=language)
        else:
            # Simple TwiML with just speech
            response.say(initial_message, voice=voice, language=language)
        
        logger.info(f"Generated TwiML response for call {call_sid}")
        return Response(content=str(response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Error handling incoming call: {str(e)}")
        # Return a simple error TwiML instead of HTTP 500
        from twilio.twiml.voice_response import VoiceResponse
        response = VoiceResponse()
        response.say("An application error has occurred. Please try again later.", voice="alice", language="en-US")
        return Response(content=str(response), media_type="application/xml")

@router.post("/call-status")
async def handle_call_status(
    request: Request,
    twilio_service: TwilioVoiceService = Depends(get_twilio_service),
    db: Session = Depends(get_db)
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
            timestamp=datetime.utcnow(),
            db_session=db
        )
        
        # Handle call completion
        if call_status == "completed":
            logger.info(f"Call {call_sid} completed after {call_duration} seconds")
            
            # Clean up conversation manager
            if call_sid in conversation_managers:
                try:
                    conversation_manager = conversation_managers[call_sid]
                    # Generate call summary if needed
                    # await conversation_manager.end_call(call_sid)
                    del conversation_managers[call_sid]
                    logger.info(f"Cleaned up conversation manager for call {call_sid}")
                except Exception as e:
                    logger.error(f"Error cleaning up conversation manager for call {call_sid}: {str(e)}")
            
        return Response(content="OK", media_type="text/plain")
        
    except Exception as e:
        logger.error(f"Error handling call status: {str(e)}")
        return Response(content="Error", media_type="text/plain", status_code=500)
    
    @router.post("/gather-callback")
    async def handle_gather_callback(
        request: Request,
        twilio_service: TwilioVoiceService = Depends(get_twilio_service),
        db: Session = Depends(get_db)
    ):
        """Handle user input from Gather verb with RAG integration"""
        try:
            form_data = await request.form()
            call_sid = form_data.get("CallSid")
            speech_result = form_data.get("SpeechResult")
            digits = form_data.get("Digits")
            
            # Get company and agent info
            company_api_key = request.query_params.get("company_key") or settings.default_company_api_key
            agent_id = request.query_params.get("agent_id") or settings.default_agent_id
            voice = request.query_params.get("voice", "alice")
            language = request.query_params.get("language", "en-US")
            
            logger.info(f"Gather callback for call {call_sid}: speech='{speech_result}'")
            
            conversation_manager = conversation_managers.get(call_sid)
            
            from twilio.twiml.voice_response import VoiceResponse, Gather
            response = VoiceResponse()
            
            if speech_result:
                try:
                    db.add(ConversationTurn(
                        call_sid=call_sid,
                        role="user",
                        content=speech_result,
                        created_at=datetime.utcnow()
                    ))
                    db.commit()
                except Exception as e:
                    logger.error(f"Error saving user turn: {str(e)}")
                
                company = db.query(Company).filter(Company.api_key == company_api_key).first()
                company_id = company.id if company else "default"
                
                if conversation_manager:
                    try:
                        logger.info(f"🔍 Querying RAG for company {company_id}, agent {agent_id}")
                        
                        llm_response = await conversation_manager.process_user_input_with_rag(
                            call_sid=call_sid,
                            user_input=speech_result,
                            company_id=company_id,
                            agent_id=agent_id
                        )
                        
                        logger.info(f"RAG response: {llm_response[:100]}...")
                        
                        try:
                            db.add(ConversationTurn(
                                call_sid=call_sid,
                                role="assistant",
                                content=llm_response,
                                created_at=datetime.utcnow()
                            ))
                            db.commit()
                        except Exception as e:
                            logger.error(f"Error saving assistant turn: {str(e)}")
                        
                        gather = Gather(
                            input='speech dtmf',
                            timeout=15,
                            speech_timeout='auto',
                            action=f'/api/v1/twilio/gather-callback?company_key={company_api_key}&agent_id={agent_id}&voice={voice}&language={language}',
                            method='POST',
                            speech_model='phone_call',
                            enhanced='true'
                        )
                        gather.say(llm_response, voice=voice, language=language)
                        response.append(gather)
                        
                        response.say("I didn't hear anything. Please try again or say goodbye to end the call.", voice=voice, language=language)
                        
                    except Exception as e:
                        logger.error(f"Error processing with RAG: {str(e)}")
                        response.say("I'm having trouble accessing my knowledge base. Please try again.", voice=voice, language=language)
                else:
                    response.say(f"You said: {speech_result}. Thank you for your input!", voice=voice, language=language)
            
            else:
                response.say("I didn't receive any input. Please speak clearly.", voice=voice, language=language)
            
            return Response(content=str(response), media_type="application/xml")
            
        except Exception as e:
            logger.error(f"Error handling gather callback: {str(e)}")
            from twilio.twiml.voice_response import VoiceResponse
            response = VoiceResponse()
            response.say("An error occurred. Goodbye!", voice="alice", language="en-US")
            return Response(content=str(response), media_type="application/xml")


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
        # Allow override via environment variable for ngrok/production
        webhook_base_url = data.get("webhook_base_url") or settings.webhook_base_url
        
        if webhook_base_url:
            # Use provided webhook base URL (for ngrok or production)
            base_url = webhook_base_url.rstrip('/')
        else:
            # Use http for localhost, https for production
            host = request.headers.get('host')
            if 'localhost' in host or '127.0.0.1' in host:
                base_url = f"http://{host}"
            else:
                base_url = f"https://{host}"
            
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

@router.get("/test-llm")
async def test_llm_service():
    """Test LLM service functionality"""
    try:
        # Test LLM service
        test_message = "Hello, this is a test message."
        response = await llm_service.generate_response([
            {"role": "user", "content": test_message}
        ])
        
        return {
            "success": True,
            "test_message": test_message,
            "llm_response": response,
            "message": "LLM service is working correctly"
        }
        
    except Exception as e:
        logger.error(f"LLM service test failed: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "message": "LLM service test failed"
        }

@router.get("/test-llm-providers")
async def test_llm_providers():
    """Test which LLM providers are available"""
    try:
        providers = {
            "claude": bool(llm_service.claude_api_key),
            "openai": bool(llm_service.openai_api_key),
            "fallback": True  # Always available
        }
        
        # Test each available provider
        test_results = {}
        test_message = "Hello, this is a test message."
        
        if providers["claude"]:
            try:
                response = await llm_service._generate_claude_response([
                    {"role": "user", "content": test_message}
                ])
                test_results["claude"] = {"status": "success", "response": response[:100] + "..."}
            except Exception as e:
                test_results["claude"] = {"status": "error", "error": str(e)}
        
        if providers["openai"]:
            try:
                response = await llm_service._generate_openai_response([
                    {"role": "user", "content": test_message}
                ])
                test_results["openai"] = {"status": "success", "response": response[:100] + "..."}
            except Exception as e:
                test_results["openai"] = {"status": "error", "error": str(e)}
        
        # Test fallback
        try:
            response = await llm_service._generate_fallback_response([
                {"role": "user", "content": test_message}
            ])
            test_results["fallback"] = {"status": "success", "response": response}
        except Exception as e:
            test_results["fallback"] = {"status": "error", "error": str(e)}
        
        return {
            "success": True,
            "available_providers": providers,
            "test_results": test_results,
            "message": "LLM provider test completed"
        }
        
    except Exception as e:
        logger.error(f"LLM provider test failed: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "message": "LLM provider test failed"
        }

@router.get("/active-conversations")
async def get_active_conversations():
    """Get list of active conversations"""
    try:
        active_calls = list(conversation_managers.keys())
        return {
            "active_conversations": len(active_calls),
            "call_sids": active_calls
        }
    except Exception as e:
        logger.error(f"Error getting active conversations: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/test-gather")
async def test_gather():
    """Test Gather TwiML generation"""
    try:
        from twilio.twiml.voice_response import VoiceResponse, Gather
        
        response = VoiceResponse()
        
        gather = Gather(
            input='speech dtmf',
            timeout=15,
            speech_timeout='auto',
            action='/api/v1/twilio/gather-callback?test=true',
            method='POST',
            speech_model='phone_call',
            enhanced='true'
        )
        gather.say("Hello! This is a test. Please say something or press a key.", voice="alice", language="en-US")
        response.append(gather)
        
        response.say("No input received. Test complete.", voice="alice", language="en-US")
        
        return Response(content=str(response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Test gather failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/test-simple-gather")
async def test_simple_gather():
    """Simple test for Gather without conversation management"""
    try:
        from twilio.twiml.voice_response import VoiceResponse, Gather
        
        response = VoiceResponse()
        
        gather = Gather(
            input='speech dtmf',
            timeout=20,  # Longer timeout for testing
            action='/api/v1/twilio/simple-gather-callback',
            method='POST'
        )
        gather.say("Hello! This is a simple test. Please say something or press any key.", voice="alice", language="en-US")
        response.append(gather)
        
        response.say("No input received. Test complete.", voice="alice", language="en-US")
        
        return Response(content=str(response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Simple gather test failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/simple-gather-callback")
async def simple_gather_callback(request: Request):
    """Simple gather callback for testing"""
    try:
        form_data = await request.form()
        
        # Log all form data for debugging
        logger.info(f"Simple gather callback - All form data: {dict(form_data)}")
        
        speech_result = form_data.get("SpeechResult")
        digits = form_data.get("Digits")
        confidence = form_data.get("Confidence")
        
        from twilio.twiml.voice_response import VoiceResponse
        
        response = VoiceResponse()
        
        if speech_result:
            response.say(f"You said: {speech_result}. Confidence: {confidence}. Test successful!", voice="alice", language="en-US")
        elif digits:
            response.say(f"You pressed: {digits}. Test successful!", voice="alice", language="en-US")
        else:
            response.say("No speech or digits detected. Please try again.", voice="alice", language="en-US")
        
        return Response(content=str(response), media_type="application/xml")
        
    except Exception as e:
        logger.error(f"Simple gather callback failed: {str(e)}")
        from twilio.twiml.voice_response import VoiceResponse
        response = VoiceResponse()
        response.say("Error occurred during test.", voice="alice", language="en-US")
        return Response(content=str(response), media_type="application/xml")
