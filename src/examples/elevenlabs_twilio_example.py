"""
ElevenLabs Twilio Integration Example
Demonstrates how to use the ElevenLabs Twilio integration for voice synthesis in calls
"""
import asyncio
import json
import logging
from typing import Dict, Any
import httpx

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ElevenLabsTwilioExample:
    """Example class demonstrating ElevenLabs Twilio integration usage"""
    
    def __init__(self, base_url: str = "http://localhost:8001"):
        self.base_url = base_url
        self.api_base = f"{base_url}/api/v1/elevenlabs-twilio"
        self.webrtc_base = f"{base_url}/api/v1/webrtc"
        
    async def check_health(self) -> Dict[str, Any]:
        """Check the health of the ElevenLabs Twilio integration"""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.api_base}/health")
            return response.json()
    
    async def get_available_voices(self) -> list:
        """Get list of available ElevenLabs voices"""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.api_base}/voices")
            return response.json()
    
    async def create_integrated_call(
        self,
        to_number: str,
        from_number: str,
        webhook_url: str,
        voice_id: str = None,
        voice_settings: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Create a Twilio call with ElevenLabs voice integration"""
        payload = {
            "to_number": to_number,
            "from_number": from_number,
            "webhook_url": webhook_url
        }
        
        if voice_id:
            payload["voice_id"] = voice_id
            
        if voice_settings:
            payload["voice_settings"] = voice_settings
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/calls",
                json=payload
            )
            return response.json()
    
    async def handle_incoming_call(
        self,
        call_sid: str,
        from_number: str,
        to_number: str,
        company_api_key: str,
        agent_id: str,
        voice_id: str = None
    ) -> Dict[str, Any]:
        """Handle an incoming call with ElevenLabs voice integration"""
        payload = {
            "call_sid": call_sid,
            "from_number": from_number,
            "to_number": to_number,
            "company_api_key": company_api_key,
            "agent_id": agent_id
        }
        
        if voice_id:
            payload["voice_id"] = voice_id
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/calls/incoming",
                json=payload
            )
            return response.json()
    
    async def synthesize_text_for_call(
        self,
        call_sid: str,
        text: str,
        voice_id: str = None
    ) -> bytes:
        """Synthesize text to audio for a specific call"""
        payload = {"text": text}
        
        if voice_id:
            payload["voice_id"] = voice_id
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/calls/{call_sid}/synthesize",
                json=payload
            )
            return response.content
    
    async def stream_text_to_call(
        self,
        call_sid: str,
        text: str
    ) -> Dict[str, Any]:
        """Stream text to ElevenLabs for real-time synthesis in a call"""
        payload = {"text": text}
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/calls/{call_sid}/stream",
                json=payload
            )
            return response.json()
    
    async def stop_call_synthesis(self, call_sid: str) -> Dict[str, Any]:
        """Stop current synthesis for a specific call"""
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.api_base}/calls/{call_sid}/stop")
            return response.json()
    
    async def get_call_info(self, call_sid: str) -> Dict[str, Any]:
        """Get integration information for a specific call"""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.api_base}/calls/{call_sid}")
            return response.json()
    
    async def update_call_voice(
        self,
        call_sid: str,
        voice_id: str = None,
        voice_settings: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Update voice settings for a specific call"""
        params = {}
        if voice_id:
            params["voice_id"] = voice_id
        
        payload = None
        if voice_settings:
            payload = voice_settings
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/calls/{call_sid}/voice",
                params=params,
                json=payload
            )
            return response.json()
    
    async def end_call(self, call_sid: str) -> Dict[str, Any]:
        """End an integrated call and cleanup resources"""
        async with httpx.AsyncClient() as client:
            response = await client.delete(f"{self.api_base}/calls/{call_sid}")
            return response.json()
    
    async def validate_voice_id(self, voice_id: str) -> Dict[str, Any]:
        """Validate if a voice ID exists"""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.api_base}/voices/{voice_id}/validate")
            return response.json()

async def run_comprehensive_example():
    """Run a comprehensive example of the ElevenLabs Twilio integration"""
    example = ElevenLabsTwilioExample()
    
    logger.info("=== ElevenLabs Twilio Integration Example ===")
    
    try:
        # 1. Check health
        logger.info("1. Checking integration health...")
        health = await example.check_health()
        logger.info(f"Health status: {health}")
        
        # 2. Get available voices
        logger.info("2. Getting available voices...")
        voices = await example.get_available_voices()
        logger.info(f"Found {len(voices)} available voices")
        
        if voices:
            # Use the first available voice
            voice_id = voices[0]["voice_id"]
            logger.info(f"Using voice: {voices[0]['name']} (ID: {voice_id})")
        else:
            voice_id = "IKne3meq5aSn9XLyUdCD"  # Default voice
            logger.info(f"Using default voice ID: {voice_id}")
        
        # 3. Validate voice ID
        logger.info("3. Validating voice ID...")
        validation = await example.validate_voice_id(voice_id)
        logger.info(f"Voice validation: {validation}")
        
        # 4. Create an integrated call (example)
        logger.info("4. Creating integrated call (example)...")
        call_data = await example.create_integrated_call(
            to_number="+1234567890",
            from_number="+0987654321",
            webhook_url="https://your-webhook-url.com/call-events",
            voice_id=voice_id,
            voice_settings={
                "stability": 0.7,
                "similarity_boost": 0.8,
                "style": 0.1,
                "use_speaker_boost": True
            }
        )
        logger.info(f"Created call: {call_data}")
        
        call_sid = call_data["call_sid"]
        
        # 5. Get call information
        logger.info("5. Getting call information...")
        call_info = await example.get_call_info(call_sid)
        logger.info(f"Call info: {call_info}")
        
        # 6. Stream text to call
        logger.info("6. Streaming text to call...")
        stream_result = await example.stream_text_to_call(
            call_sid=call_sid,
            text="Hello! This is a test of the ElevenLabs Twilio integration. How can I help you today?"
        )
        logger.info(f"Stream result: {stream_result}")
        
        # 7. Update voice settings
        logger.info("7. Updating voice settings...")
        update_result = await example.update_call_voice(
            call_sid=call_sid,
            voice_settings={
                "stability": 0.8,
                "similarity_boost": 0.9
            }
        )
        logger.info(f"Update result: {update_result}")
        
        # 8. Synthesize text for call (REST API)
        logger.info("8. Synthesizing text using REST API...")
        audio_data = await example.synthesize_text_for_call(
            call_sid=call_sid,
            text="This audio was generated using the REST API endpoint.",
            voice_id=voice_id
        )
        logger.info(f"Generated audio: {len(audio_data)} bytes")
        
        # 9. Stop synthesis
        logger.info("9. Stopping synthesis...")
        stop_result = await example.stop_call_synthesis(call_sid)
        logger.info(f"Stop result: {stop_result}")
        
        # 10. End call
        logger.info("10. Ending call...")
        end_result = await example.end_call(call_sid)
        logger.info(f"End result: {end_result}")
        
        logger.info("=== Example completed successfully! ===")
        
    except Exception as e:
        logger.error(f"Example failed: {str(e)}")

async def run_webhook_example():
    """Example of handling incoming calls with webhooks"""
    example = ElevenLabsTwilioExample()
    
    logger.info("=== Webhook Example ===")
    
    try:
        # Simulate incoming call webhook
        webhook_data = await example.handle_incoming_call(
            call_sid="CA1234567890abcdef",
            from_number="+1234567890",
            to_number="+0987654321",
            company_api_key="your-company-api-key",
            agent_id="agent-123",
            voice_id="IKne3meq5aSn9XLyUdCD"
        )
        
        logger.info(f"Webhook response: {webhook_data}")
        
        # The webhook response contains TwiML that Twilio will use
        # to connect the call to the ElevenLabs integration
        
    except Exception as e:
        logger.error(f"Webhook example failed: {str(e)}")

async def run_voice_management_example():
    """Example of voice management features"""
    example = ElevenLabsTwilioExample()
    
    logger.info("=== Voice Management Example ===")
    
    try:
        # Get all available voices
        voices = await example.get_available_voices()
        
        logger.info("Available voices:")
        for voice in voices[:5]:  # Show first 5 voices
            logger.info(f"  - {voice['name']} (ID: {voice['voice_id']})")
            logger.info(f"    Category: {voice['category']}")
            if voice.get('description'):
                logger.info(f"    Description: {voice['description']}")
            logger.info("")
        
        # Validate specific voice IDs
        test_voice_ids = ["EXAVITQu4vr4xnSDxMaL", "invalid-voice-id"]
        
        for voice_id in test_voice_ids:
            validation = await example.validate_voice_id(voice_id)
            logger.info(f"Voice {voice_id} is valid: {validation['valid']}")
        
    except Exception as e:
        logger.error(f"Voice management example failed: {str(e)}")

def print_usage_instructions():
    """Print usage instructions for the integration"""
    print("""
=== ElevenLabs Twilio Integration Usage Instructions ===

1. Environment Variables Required:
   - ELEVEN_LABS_API_KEY: Your ElevenLabs API key
   - TWILIO_ACCOUNT_SID: Your Twilio Account SID
   - TWILIO_AUTH_TOKEN: Your Twilio Auth Token
   - TWILIO_PHONE_NUMBER: Your Twilio phone number
   - VOICE_ID: Default ElevenLabs voice ID (optional)

2. API Endpoints:
   - Health Check: GET /api/v1/elevenlabs-twilio/health
   - Get Voices: GET /api/v1/elevenlabs-twilio/voices
   - Create Call: POST /api/v1/elevenlabs-twilio/calls
   - Handle Incoming: POST /api/v1/elevenlabs-twilio/calls/incoming
   - Synthesize Text: POST /api/v1/elevenlabs-twilio/calls/{call_sid}/synthesize
   - Stream Text: POST /api/v1/elevenlabs-twilio/calls/{call_sid}/stream
   - Stop Synthesis: POST /api/v1/elevenlabs-twilio/calls/{call_sid}/stop
   - Get Call Info: GET /api/v1/elevenlabs-twilio/calls/{call_sid}
   - Update Voice: POST /api/v1/elevenlabs-twilio/calls/{call_sid}/voice
   - End Call: DELETE /api/v1/elevenlabs-twilio/calls/{call_sid}

3. WebRTC Endpoints:
   - WebSocket Stream: ws://host/api/v1/webrtc/twilio-elevenlabs-stream/{peer_id}/{company_api_key}/{agent_id}
   - Connection Status: GET /api/v1/webrtc/connections/{peer_id}/status
   - All Connections: GET /api/v1/webrtc/connections

4. Voice Settings:
   - stability: 0.0-1.0 (voice stability)
   - similarity_boost: 0.0-1.0 (voice similarity)
   - style: 0.0-1.0 (voice style)
   - use_speaker_boost: boolean (enable speaker boost)

5. Example Usage:
   ```python
   # Create a call with custom voice
   call = await create_integrated_call(
       to_number="+1234567890",
       from_number="+0987654321", 
       webhook_url="https://your-webhook.com",
       voice_id="your-voice-id",
       voice_settings={"stability": 0.8}
   )
   
   # Stream text to call
   await stream_text_to_call(call["call_sid"], "Hello, how can I help you?")
   ```

6. WebSocket Messages:
   - Send text: {"type": "text", "text": "Hello world"}
   - Control: {"type": "control", "action": "stop_synthesis"}
   - Ping: {"type": "ping"}

7. Error Handling:
   - All endpoints return appropriate HTTP status codes
   - WebSocket connections handle disconnections gracefully
   - Audio streaming includes error recovery mechanisms

8. Best Practices:
   - Always validate voice IDs before use
   - Use appropriate voice settings for your use case
   - Handle WebSocket disconnections gracefully
   - Monitor call status and cleanup resources
   - Use streaming for real-time synthesis, REST API for pre-generated audio
""")

if __name__ == "__main__":
    # Print usage instructions
    print_usage_instructions()
    
    # Run examples
    asyncio.run(run_comprehensive_example())
    asyncio.run(run_webhook_example())
    asyncio.run(run_voice_management_example())
