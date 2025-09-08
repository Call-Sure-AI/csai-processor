"""
Twilio-ElevenLabs Integration Service
Combines Twilio call management with ElevenLabs voice synthesis
"""
import asyncio
import logging
import json
import base64
from typing import Dict, Any, Optional, Callable, List
from datetime import datetime
import uuid
import tempfile
import os
from twilio.twiml.voice_response import VoiceResponse, Connect, Start, Stream, Gather, Play
from .twilio_service import TwilioVoiceService
from .elevenlabs_service import ElevenLabsVoiceService
from config.settings import settings

logger = logging.getLogger(__name__)

class TwilioElevenLabsIntegration:
    """Integration service for Twilio calls with ElevenLabs voice synthesis"""
    
    def __init__(self):
        self.twilio_service = TwilioVoiceService()
        self.elevenlabs_service = ElevenLabsVoiceService()
        self.active_integrations: Dict[str, Dict[str, Any]] = {}
        self.audio_streams: Dict[str, asyncio.Queue] = {}
        self.call_handlers: Dict[str, Callable] = {}
        self._initialized = False
        
    async def initialize(self):
        """Initialize both Twilio and ElevenLabs services"""
        if self._initialized:
            return True
            
        try:
            # Initialize Twilio service
            await self.twilio_service.initialize()
            
            # Initialize ElevenLabs service
            await self.elevenlabs_service.initialize()
            
            self._initialized = True
            logger.info("Twilio-ElevenLabs integration initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize integration: {str(e)}")
            self._initialized = False
            return False
    
    async def cleanup(self):
        """Cleanup all resources"""
        try:
            # Cleanup ElevenLabs service
            await self.elevenlabs_service.cleanup()
            
            # Cleanup active integrations
            for call_sid in list(self.active_integrations.keys()):
                await self.end_integrated_call(call_sid)
                
            logger.info("Twilio-ElevenLabs integration cleaned up")
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")
    
    async def create_integrated_call(
        self,
        to_number: str,
        from_number: str,
        webhook_url: str,
        voice_id: Optional[str] = None,
        voice_settings: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Create a Twilio call with ElevenLabs voice integration"""
        try:
            # Create the webhook URL with voice parameters
            webhook_with_params = f"{webhook_url}?voice_id={voice_id}" if voice_id else webhook_url
            
            # Create the Twilio call
            call_data = await self.twilio_service.create_call(
                to_number=to_number,
                from_number=from_number,
                webhook_url=webhook_with_params,
                **kwargs
            )
            
            call_sid = call_data['call_sid']
            
            # Set up ElevenLabs voice if specified
            if voice_id:
                await self.elevenlabs_service.set_voice(voice_id)
            
            if voice_settings:
                await self.elevenlabs_service.set_voice_settings(voice_settings)
            
            # Create audio stream queue for this call
            self.audio_streams[call_sid] = asyncio.Queue()
            
            # Store integration data
            self.active_integrations[call_sid] = {
                'call_sid': call_sid,
                'voice_id': voice_id or self.elevenlabs_service.voice_id,
                'voice_settings': voice_settings or self.elevenlabs_service.voice_settings,
                'created_at': datetime.utcnow(),
                'status': 'created'
            }
            
            logger.info(f"Created integrated call {call_sid} with ElevenLabs voice")
            return call_data
            
        except Exception as e:
            logger.error(f"Failed to create integrated call: {str(e)}")
            raise
        
    async def handle_incoming_integrated_call(
        self,
        call_sid: str,
        from_number: str,
        to_number: str,
        company_api_key: str,
        agent_id: str,
        base_url: str,
        voice_id: Optional[str] = None,
        voice_settings: Optional[Dict[str, Any]] = None
    ) -> VoiceResponse:
        """Handle incoming call with ElevenLabs voice integration"""
        try:
            logger.info(f"Starting ElevenLabs integration for call {call_sid}")
            
            # Set up ElevenLabs voice if specified (with error handling)
            try:
                if voice_id:
                    logger.info(f"Setting ElevenLabs voice to: {voice_id}")
                    await self.elevenlabs_service.set_voice(voice_id)
                
                if voice_settings:
                    logger.info(f"Setting ElevenLabs voice settings: {voice_settings}")
                    await self.elevenlabs_service.set_voice_settings(voice_settings)
            except Exception as voice_error:
                logger.warning(f"Failed to set ElevenLabs voice/settings: {str(voice_error)}")
            
            # Create audio stream queue for this call
            self.audio_streams[call_sid] = asyncio.Queue()
            
            # Store integration data
            self.active_integrations[call_sid] = {
                'call_sid': call_sid,
                'from_number': from_number,
                'to_number': to_number,
                'company_api_key': company_api_key,
                'agent_id': agent_id,
                'voice_id': voice_id or self.elevenlabs_service.voice_id,
                'voice_settings': voice_settings or self.elevenlabs_service.voice_settings,
                'created_at': datetime.utcnow(),
                'status': 'incoming'
            }
            
            # Create WebRTC stream URL with proper WebSocket protocol
            stream_url = f"wss://{base_url.replace('https://', '').replace('http://', '')}/api/v1/webrtc/twilio-direct-stream/{call_sid}"
            logger.info(f"WebRTC stream URL: {stream_url}")
            
            # Generate TwiML response with proper Stream configuration
            response = VoiceResponse()
            
            try:
                logger.info("Creating TwiML with Twilio's built-in speech recognition")
                
                # Generate greeting using LLM
                greeting = await self._generate_greeting()
                
                # Synthesize greeting with ElevenLabs and get audio URL
                audio_url = await self._synthesize_greeting_and_get_url(call_sid, greeting)
                
                if audio_url:
                    # Play ElevenLabs greeting
                    response.play(audio_url)
                    logger.info(f"Playing ElevenLabs greeting for call {call_sid}: {audio_url}")
                else:
                    # Fallback to Twilio's voice
                    response.say(greeting, voice="alice", language="en-US")
                    logger.warning(f"Using Twilio fallback voice for greeting in call {call_sid}")
                
                # Use Twilio's Gather for speech recognition with ElevenLabs voice
                gather_text = "How can I help you today?"
                gather_audio_url = await self._synthesize_greeting_and_get_url(call_sid, gather_text)
                
                gather = Gather(
                    input='speech',
                    action=f"{base_url}/api/v1/elevenlabs-twilio/calls/{call_sid}/speech",
                    method='POST',
                    speech_timeout='auto',
                    language='en-US',
                    enhanced='true'
                )
                
                if gather_audio_url:
                    gather.play(gather_audio_url)
                    logger.info(f"Playing ElevenLabs gather audio for call {call_sid}: {gather_audio_url}")
                else:
                    gather.say(gather_text, voice="alice", language="en-US")
                    logger.warning(f"Using Twilio fallback voice for gather in call {call_sid}")
                
                response.append(gather)
                
                # Fallback if no speech detected using ElevenLabs voice
                fallback_text = "I didn't hear anything. Please try again."
                fallback_audio_url = await self._synthesize_greeting_and_get_url(call_sid, fallback_text)
                
                if fallback_audio_url:
                    response.play(fallback_audio_url)
                    logger.info(f"Playing ElevenLabs fallback audio for call {call_sid}: {fallback_audio_url}")
                else:
                    response.say(fallback_text, voice="alice", language="en-US")
                    logger.warning(f"Using Twilio fallback voice for fallback in call {call_sid}")
                
                response.redirect(f"{base_url}/api/v1/elevenlabs-twilio/calls/{call_sid}/speech")
                
                logger.info(f"Generated TwiML with speech recognition for call {call_sid}")
                
            except Exception as speech_error:
                logger.error(f"Speech recognition setup failed: {str(speech_error)}")
                logger.info("Falling back to simple TwiML approach")
                
                # Clear the response and add fallback
                response = VoiceResponse()
                response.say("There is an issue with the speech recognition. Please try again later.", voice="alice", language="en-US")
            
            logger.info(f"Generated TwiML for call {call_sid} with ElevenLabs integration")
            return response
            
        except Exception as e:
            logger.error(f"Failed to handle incoming integrated call: {str(e)}", exc_info=True)
            # Return a simple fallback TwiML instead of raising
            response = VoiceResponse()
            response.say("Hello! This is a fallback response. There was an issue with the integration.", voice="alice", language="en-US")
            return response
    
    async def synthesize_and_stream(
        self,
        call_sid: str,
        text: str,
        audio_callback: Optional[Callable] = None
    ) -> bool:
        """Synthesize text to speech and stream to Twilio call"""
        if call_sid not in self.active_integrations:
            logger.error(f"Call {call_sid} not found in active integrations")
            return False
        
        try:
            # Set up audio callback for this call
            if audio_callback:
                await self.elevenlabs_service.connect(audio_callback)
            else:
                # Use default callback that adds to call's audio queue
                await self.elevenlabs_service.connect(self._default_audio_callback(call_sid))
            
            # Stream text to ElevenLabs
            success = await self.elevenlabs_service.stream_text(text)
            
            if success:
                logger.info(f"Successfully synthesized and streamed text for call {call_sid}")
                return True
            else:
                logger.error(f"Failed to synthesize text for call {call_sid}")
                return False
                
        except Exception as e:
            logger.error(f"Error in synthesize_and_stream for call {call_sid}: {str(e)}")
            return False
    
    def _default_audio_callback(self, call_sid: str):
        """Default audio callback that adds audio to call's queue"""
        async def callback(audio_data: bytes):
            if call_sid in self.audio_streams:
                await self.audio_streams[call_sid].put(audio_data)
        return callback
    
    async def _generate_greeting(self) -> str:
        """Generate a greeting using LLM service"""
        try:
            from services.llm_service import MultiProviderLLMService
            llm_service = MultiProviderLLMService()
            
            agent_config = {
                "name": "Customer Service Agent",
                "personality": "friendly and helpful",
                "context": "customer service call"
            }
            greeting = await llm_service.generate_greeting(agent_config)
            logger.info(f"Generated greeting: '{greeting}'")
            return greeting
        except Exception as e:
            logger.error(f"Failed to generate greeting: {str(e)}")
            return "Hello! Thank you for calling. I'm here to help you today."
    
    async def _synthesize_greeting_and_get_url(self, call_sid: str, greeting: str) -> Optional[str]:
        """Synthesize greeting with ElevenLabs and return audio URL"""
        try:
            # Get the voice ID from the active integration
            if call_sid in self.active_integrations:
                integration_info = self.active_integrations[call_sid]
                voice_id = integration_info.get('voice_id', 'default')
                logger.info(f"Using ElevenLabs voice ID: {voice_id} for greeting in call {call_sid}")
            else:
                voice_id = 'default'
                logger.warning(f"Call {call_sid} not found in active integrations, using default voice for greeting")
            
            # Synthesize greeting with ElevenLabs
            audio_data = await self.synthesize_text_for_call(
                call_sid=call_sid,
                text=greeting
            )
            
            if audio_data:
                # Return the audio data - the routes will handle saving and URL generation
                # We'll store it temporarily in the integration for the routes to access
                if not hasattr(self, 'temp_audio_data'):
                    self.temp_audio_data = {}
                
                audio_id = f"greeting_{call_sid}_{uuid.uuid4().hex[:8]}"
                self.temp_audio_data[audio_id] = audio_data
                
                base_url = settings.base_url or settings.webhook_base_url
                audio_url = f"{base_url}/api/v1/elevenlabs-twilio/audio/{audio_id}"
                
                logger.info(f"Generated greeting audio URL for call {call_sid}: {audio_url}")
                return audio_url
            else:
                logger.error(f"Failed to synthesize greeting audio for call {call_sid}")
                return None
                
        except Exception as e:
            logger.error(f"Error synthesizing greeting for call {call_sid}: {str(e)}")
            return None

    async def _synthesize_greeting_background(self, call_sid: str, greeting: str):
        """Synthesize greeting with ElevenLabs in the background"""
        try:
            # Add a small delay to ensure the call is established
            await asyncio.sleep(1.0)
            
            # Synthesize and stream the greeting
            success = await self.synthesize_and_stream(
                call_sid=call_sid,
                text=greeting
            )
            
            if success:
                logger.info(f"Successfully synthesized greeting with ElevenLabs for call {call_sid}")
            else:
                logger.error(f"Failed to synthesize greeting for call {call_sid}")
                
        except Exception as e:
            logger.error(f"Error synthesizing greeting in background for call {call_sid}: {str(e)}")
    
    async def get_call_audio_stream(self, call_sid: str) -> Optional[asyncio.Queue]:
        """Get the audio stream queue for a specific call"""
        return self.audio_streams.get(call_sid)
    
    async def synthesize_text_for_call(
        self,
        call_sid: str,
        text: str
    ) -> Optional[bytes]:
        """Synthesize text to audio for a specific call using REST API"""
        if call_sid not in self.active_integrations:
            logger.error(f"Call {call_sid} not found in active integrations")
            return None
        
        try:
            # Use the call's voice settings
            integration_data = self.active_integrations[call_sid]
            original_voice_id = self.elevenlabs_service.voice_id
            original_settings = self.elevenlabs_service.voice_settings.copy()
            
            # Temporarily set call-specific voice settings
            await self.elevenlabs_service.set_voice(integration_data['voice_id'])
            await self.elevenlabs_service.set_voice_settings(integration_data['voice_settings'])
            
            # Synthesize text
            audio_data = await self.elevenlabs_service.synthesize_text(text)
            
            # Restore original settings
            await self.elevenlabs_service.set_voice(original_voice_id)
            await self.elevenlabs_service.set_voice_settings(original_settings)
            
            return audio_data
            
        except Exception as e:
            logger.error(f"Error synthesizing text for call {call_sid}: {str(e)}")
            return None
    
    async def update_call_voice(
        self,
        call_sid: str,
        voice_id: Optional[str] = None,
        voice_settings: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Update voice settings for a specific call"""
        if call_sid not in self.active_integrations:
            logger.error(f"Call {call_sid} not found in active integrations")
            return False
        
        try:
            integration_data = self.active_integrations[call_sid]
            
            if voice_id:
                integration_data['voice_id'] = voice_id
                await self.elevenlabs_service.set_voice(voice_id)
            
            if voice_settings:
                integration_data['voice_settings'].update(voice_settings)
                await self.elevenlabs_service.set_voice_settings(voice_settings)
            
            logger.info(f"Updated voice settings for call {call_sid}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating voice for call {call_sid}: {str(e)}")
            return False
    
    async def stop_call_synthesis(self, call_sid: str) -> bool:
        """Stop current synthesis for a specific call"""
        try:
            await self.elevenlabs_service.stop_playback()
            
            # Clear call's audio queue
            if call_sid in self.audio_streams:
                while not self.audio_streams[call_sid].empty():
                    try:
                        self.audio_streams[call_sid].get_nowait()
                        self.audio_streams[call_sid].task_done()
                    except asyncio.QueueEmpty:
                        break
            
            logger.info(f"Stopped synthesis for call {call_sid}")
            return True
            
        except Exception as e:
            logger.error(f"Error stopping synthesis for call {call_sid}: {str(e)}")
            return False
    
    async def end_integrated_call(self, call_sid: str) -> bool:
        """End an integrated call and cleanup resources"""
        try:
            # Stop synthesis
            await self.stop_call_synthesis(call_sid)
            
            # End Twilio call
            success = await self.twilio_service.end_call(call_sid)
            
            # Cleanup integration data
            if call_sid in self.active_integrations:
                del self.active_integrations[call_sid]
            
            if call_sid in self.audio_streams:
                del self.audio_streams[call_sid]
            
            logger.info(f"Ended integrated call {call_sid}")
            return success
            
        except Exception as e:
            logger.error(f"Error ending integrated call {call_sid}: {str(e)}")
            return False
    
    async def get_integration_info(self, call_sid: str) -> Optional[Dict[str, Any]]:
        """Get integration information for a specific call"""
        integration_data = self.active_integrations.get(call_sid)
        if not integration_data:
            return None
        
        # Add ElevenLabs voice info
        voice_info = self.elevenlabs_service.get_voice_info()
        
        return {
            **integration_data,
            'elevenlabs_voice_info': voice_info,
            'audio_queue_size': self.audio_streams.get(call_sid, asyncio.Queue()).qsize() if call_sid in self.audio_streams else 0
        }
    
    async def get_all_integrations(self) -> List[Dict[str, Any]]:
        """Get information about all active integrations"""
        integrations = []
        for call_sid in self.active_integrations:
            info = await self.get_integration_info(call_sid)
            if info:
                integrations.append(info)
        return integrations
    
    async def get_available_voices(self) -> List[Dict[str, Any]]:
        """Get list of available ElevenLabs voices"""
        return await self.elevenlabs_service.get_available_voices()
    
    async def validate_voice_id(self, voice_id: str) -> bool:
        """Validate if a voice ID exists"""
        voices = await self.get_available_voices()
        return any(voice.get('voice_id') == voice_id for voice in voices)
    
    async def get_call_transcript(self, call_sid: str) -> List[Dict[str, Any]]:
        """Get transcript for a specific call (placeholder for future implementation)"""
        # This would integrate with Twilio's transcript API or your own STT service
        logger.info(f"Getting transcript for call {call_sid}")
        return []
    
    async def update_call_status(self, call_sid: str, status: str, **kwargs):
        """Update call status and metadata"""
        await self.twilio_service.update_call_status(call_sid, status, **kwargs)
        
        # Update integration status
        if call_sid in self.active_integrations:
            self.active_integrations[call_sid]['status'] = status
            self.active_integrations[call_sid]['updated_at'] = datetime.utcnow()
            self.active_integrations[call_sid].update(kwargs)
    
    def get_active_integrations_count(self) -> int:
        """Get count of active integrations"""
        return len(self.active_integrations)


# Global integration service instance
twilio_elevenlabs_integration = TwilioElevenLabsIntegration()
