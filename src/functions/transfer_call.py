from typing import Dict, Any
import logging
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse

from config.settings import settings

logger = logging.getLogger(__name__)


class TransferCallArgs:
    """Arguments for transfer call function"""
    def __init__(self, call_sid: str):
        self.call_sid = call_sid


async def transfer_call(function_args: Dict[str, Any]) -> str:
    """
    Transfer the call to a live human agent
    
    Args:
        function_args: Dictionary containing 'callSid' key
        
    Returns:
        Status message string
    """
    call_sid = function_args.get('callSid', '')
    
    logger.info(f"Transferring call {call_sid}")
    
    # Validate required environment variables
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        logger.error("Twilio credentials not configured")
        return "I apologize, but I am unable to connect you with a live agent right now. Please call back later or visit our website for assistance."
    
    # Check if transfer number is configured
    transfer_number = getattr(settings, 'transfer_number', None)
    if not transfer_number:
        logger.error("Transfer number not configured")
        return "I apologize, but I am unable to connect you with a live agent right now. Please call back later or visit our website for assistance."
    
    try:
        # Initialize Twilio client
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        
        # Create TwiML response
        response = VoiceResponse()
        
        # Play a message before transferring
        response.say(
            'Please hold while I connect you with one of our live agents. This may take a moment.',
            voice='alice',
            language='en-IN'
        )
        
        # Add a pause
        response.pause(length=2)
        
        # Transfer the call
        response.dial(transfer_number)
        
        # Update the call with new TwiML
        client.calls(call_sid).update(twiml=str(response))
        
        logger.info(f"Call {call_sid} transferred successfully to {transfer_number}")
        
        return "I am now connecting you with a live agent. Please hold on for a moment."
        
    except Exception as e:
        logger.error(f"Transfer failed for call {call_sid}: {e}")
        return "I apologize, but I am unable to connect you with a live agent right now. Please call back later or visit our website for assistance."
