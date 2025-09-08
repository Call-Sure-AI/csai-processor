# ElevenLabs Twilio Integration

A comprehensive integration between ElevenLabs voice synthesis and Twilio call management, enabling high-quality AI voice synthesis in phone calls.

## Overview

This integration provides seamless voice synthesis capabilities for Twilio calls using ElevenLabs' advanced AI voice technology. It supports both real-time streaming synthesis and pre-generated audio, with full WebRTC support for low-latency audio delivery.

## Features

- **Real-time Voice Synthesis**: Stream text to ElevenLabs for immediate voice synthesis during calls
- **Multiple Voice Support**: Choose from hundreds of ElevenLabs voices with customizable settings
- **WebRTC Integration**: Low-latency audio streaming via WebSocket connections
- **Call Management**: Full Twilio call lifecycle management with voice integration
- **Voice Customization**: Adjust stability, similarity, style, and speaker boost settings
- **Error Recovery**: Robust error handling and automatic reconnection
- **REST API**: Complete REST API for programmatic control
- **WebSocket API**: Real-time communication for dynamic voice control

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Twilio Call   │    │  ElevenLabs API  │    │  WebRTC Stream  │
│                 │    │                  │    │                 │
│  ┌───────────┐  │    │  ┌────────────┐  │    │  ┌───────────┐  │
│  │   Call    │◄─┼────┼─►│  Voice     │  │    │  │  Audio    │  │
│  │  Events   │  │    │  │ Synthesis  │  │    │  │  Stream   │  │
│  └───────────┘  │    │  └────────────┘  │    │  └───────────┘  │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                       │                       │
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Integration Service                          │
│                                                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │  Twilio Service │  │ ElevenLabs      │  │  WebRTC Manager │ │
│  │                 │  │ Service         │  │                 │ │
│  │ • Call Creation │  │ • Voice         │  │ • Connection    │ │
│  │ • Call Control  │  │   Synthesis     │  │   Management    │ │
│  │ • Status Updates│  │ • Voice Settings│  │ • Audio Routing │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Installation

### Prerequisites

- Python 3.8+
- FastAPI
- Twilio account with phone number
- ElevenLabs API key

### Environment Variables

```bash
# ElevenLabs Configuration
ELEVEN_LABS_API_KEY=your_elevenlabs_api_key
VOICE_ID=IKne3meq5aSn9XLyUdCD  # Default voice ID

# Twilio Configuration
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_PHONE_NUMBER=your_twilio_phone_number

# Application Configuration
BASE_URL=http://localhost:8001
WEBHOOK_BASE_URL=https://your-domain.com
```

### Dependencies

The integration uses the following key dependencies:

```python
# Core dependencies (already in requirements.txt)
fastapi>=0.104.0
twilio>=8.10.0
aiohttp>=3.9.0
httpx>=0.25.0
pydub>=0.25.0
```

## API Reference

### Base URL

```
https://your-domain.com/api/v1/elevenlabs-twilio
```

### Authentication

All endpoints require proper configuration of ElevenLabs and Twilio credentials via environment variables.

### Webhook URLs

There are two types of webhook URLs used in this integration:

1. **Call Webhook URL** (`webhook_url`): This is the URL that Twilio calls when a call connects to get TwiML instructions. It should point to `/api/v1/elevenlabs-twilio/webhook`.

2. **Status Callback URL** (`status_callback`): This is the URL where Twilio sends call status updates (initiated, ringing, answered, completed). It should point to `/api/v1/twilio/call-status`.

**Important**: Do not confuse these two URLs. The call webhook returns TwiML, while the status callback receives status updates.

### Endpoints

#### Health Check

```http
GET /health
```

Check the health status of the integration.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2024-01-01T12:00:00Z",
  "active_integrations": 5,
  "elevenlabs_configured": true,
  "twilio_configured": true
}
```

#### Get Available Voices

```http
GET /voices
```

Retrieve all available ElevenLabs voices.

**Response:**
```json
[
  {
    "voice_id": "IKne3meq5aSn9XLyUdCD",
    "name": "Rachel",
    "category": "premade",
    "description": "A warm and friendly voice",
    "labels": {
      "accent": "american",
      "gender": "female"
    }
  }
]
```

#### Create Integrated Call

```http
POST /calls
```

Create a new Twilio call with ElevenLabs voice integration.

**Request Body:**
```json
{
  "to_number": "+1234567890",
  "from_number": "+0987654321",
  "webhook_url": "https://your-webhook.com/events",
  "voice_id": "IKne3meq5aSn9XLyUdCD",
  "voice_settings": {
    "stability": 0.7,
    "similarity_boost": 0.8,
    "style": 0.1,
    "use_speaker_boost": true
  },
  "metadata": {
    "customer_id": "12345",
    "call_type": "support"
  }
}
```

**Response:**
```json
{
  "call_sid": "CA1234567890abcdef",
  "status": "initiated",
  "direction": "outbound-api",
  "to": "+1234567890",
  "from_": "+0987654321",
  "created_at": "2024-01-01T12:00:00Z",
  "voice_id": "IKne3meq5aSn9XLyUdCD",
  "voice_settings": {
    "stability": 0.7,
    "similarity_boost": 0.8,
    "style": 0.1,
    "use_speaker_boost": true
  }
}
```

#### Handle Incoming Call

```http
POST /calls/incoming
```

Handle an incoming call with ElevenLabs voice integration.

**Request Body:**
```json
{
  "call_sid": "CA1234567890abcdef",
  "from_number": "+1234567890",
  "to_number": "+0987654321",
  "company_api_key": "your-company-key",
  "agent_id": "agent-123",
  "voice_id": "IKne3meq5aSn9XLyUdCD",
  "voice_settings": {
    "stability": 0.8,
    "similarity_boost": 0.9
  }
}
```

**Response:**
```json
{
  "call_sid": "CA1234567890abcdef",
  "twiml": "<?xml version=\"1.0\" encoding=\"UTF-8\"?>...",
  "status": "handled"
}
```

#### Synthesize Text for Call

```http
POST /calls/{call_sid}/synthesize
```

Synthesize text to audio for a specific call using REST API.

**Request Body:**
```json
{
  "text": "Hello! How can I help you today?",
  "voice_id": "IKne3meq5aSn9XLyUdCD",
  "voice_settings": {
    "stability": 0.8
  }
}
```

**Response:** Audio file (MP3 format)

#### Stream Text to Call

```http
POST /calls/{call_sid}/stream
```

Stream text to ElevenLabs for real-time synthesis in a call.

**Request Body:**
```json
{
  "text": "Hello! How can I help you today?"
}
```

**Response:**
```json
{
  "call_sid": "CA1234567890abcdef",
  "status": "streaming",
  "text_length": 35
}
```

#### Stop Call Synthesis

```http
POST /calls/{call_sid}/stop
```

Stop current synthesis for a specific call.

**Response:**
```json
{
  "call_sid": "CA1234567890abcdef",
  "status": "stopped"
}
```

#### Get Call Integration Info

```http
GET /calls/{call_sid}
```

Get integration information for a specific call.

**Response:**
```json
{
  "call_sid": "CA1234567890abcdef",
  "voice_id": "IKne3meq5aSn9XLyUdCD",
  "voice_settings": {
    "stability": 0.7,
    "similarity_boost": 0.8,
    "style": 0.1,
    "use_speaker_boost": true
  },
  "status": "in-progress",
  "created_at": "2024-01-01T12:00:00Z",
  "audio_queue_size": 3,
  "elevenlabs_voice_info": {
    "voice_id": "IKne3meq5aSn9XLyUdCD",
    "voice_settings": {...},
    "is_connected": true,
    "has_initial_message": true
  }
}
```

#### Update Call Voice Settings

```http
POST /calls/{call_sid}/voice
```

Update voice settings for a specific call.

**Query Parameters:**
- `voice_id` (optional): New voice ID

**Request Body:**
```json
{
  "stability": 0.9,
  "similarity_boost": 0.95
}
```

**Response:**
```json
{
  "call_sid": "CA1234567890abcdef",
  "status": "updated",
  "voice_id": "new-voice-id",
  "voice_settings": {
    "stability": 0.9,
    "similarity_boost": 0.95
  }
}
```

#### End Integrated Call

```http
DELETE /calls/{call_sid}
```

End an integrated call and cleanup resources.

**Response:**
```json
{
  "call_sid": "CA1234567890abcdef",
  "status": "ended"
}
```

#### Validate Voice ID

```http
GET /voices/{voice_id}/validate
```

Validate if a voice ID exists.

**Response:**
```json
{
  "voice_id": "IKne3meq5aSn9XLyUdCD",
  "valid": true
}
```

## WebRTC API

### WebSocket Connection

```
ws://your-domain.com/api/v1/webrtc/twilio-elevenlabs-stream/{peer_id}/{company_api_key}/{agent_id}
```

### WebSocket Messages

#### Send Text for Synthesis

```json
{
  "type": "text",
  "text": "Hello! How can I help you today?"
}
```

#### Control Messages

```json
{
  "type": "control",
  "action": "stop_synthesis"
}
```

```json
{
  "type": "control",
  "action": "update_voice",
  "voice_id": "new-voice-id",
  "voice_settings": {
    "stability": 0.8
  }
}
```

```json
{
  "type": "control",
  "action": "get_call_info"
}
```

#### Ping/Pong

```json
{
  "type": "ping"
}
```

**Response:**
```json
{
  "type": "pong",
  "timestamp": "2024-01-01T12:00:00Z"
}
```

### WebSocket Responses

#### Connection Established

```json
{
  "type": "connection_established",
  "peer_id": "twilio_CA1234567890abcdef_uuid",
  "call_sid": "CA1234567890abcdef",
  "timestamp": "2024-01-01T12:00:00Z"
}
```

#### Audio Data

```json
{
  "type": "audio",
  "data": "base64_encoded_audio_data",
  "timestamp": "2024-01-01T12:00:00Z"
}
```

#### Synthesis Status

```json
{
  "type": "synthesis_started",
  "text": "Hello! How can I help you today?",
  "timestamp": "2024-01-01T12:00:00Z"
}
```

```json
{
  "type": "synthesis_error",
  "text": "Hello! How can I help you today?",
  "error": "Failed to synthesize text",
  "timestamp": "2024-01-01T12:00:00Z"
}
```

#### Control Response

```json
{
  "type": "control_response",
  "action": "stop_synthesis",
  "success": true,
  "timestamp": "2024-01-01T12:00:00Z"
}
```

## Voice Settings

### Stability (0.0 - 1.0)

Controls the consistency of the voice. Higher values make the voice more consistent but less expressive.

- **0.0**: Maximum expressiveness, minimum consistency
- **1.0**: Maximum consistency, minimum expressiveness
- **Recommended**: 0.5 - 0.8

### Similarity Boost (0.0 - 1.0)

Controls how closely the generated voice matches the original voice.

- **0.0**: Less similar to original voice
- **1.0**: Most similar to original voice
- **Recommended**: 0.7 - 0.9

### Style (0.0 - 1.0)

Controls the speaking style of the voice.

- **0.0**: Neutral speaking style
- **1.0**: Maximum style expression
- **Recommended**: 0.0 - 0.3

### Use Speaker Boost (boolean)

Enhances the clarity and presence of the voice.

- **true**: Enable speaker boost
- **false**: Disable speaker boost
- **Recommended**: true

## Usage Examples

### Python Client Example

```python
import asyncio
import httpx

class ElevenLabsTwilioClient:
    def __init__(self, base_url: str):
        self.base_url = f"{base_url}/api/v1/elevenlabs-twilio"
    
    async def create_call(self, to_number: str, voice_id: str):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/calls",
                json={
                    "to_number": to_number,
                    "from_number": "+1234567890",
                    "webhook_url": "https://your-webhook.com",
                    "voice_id": voice_id,
                    "voice_settings": {
                        "stability": 0.8,
                        "similarity_boost": 0.9
                    }
                }
            )
            return response.json()
    
    async def stream_text(self, call_sid: str, text: str):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/calls/{call_sid}/stream",
                json={"text": text}
            )
            return response.json()

# Usage
async def main():
    client = ElevenLabsTwilioClient("https://your-domain.com")
    
    # Create call
    call = await client.create_call("+1234567890", "IKne3meq5aSn9XLyUdCD")
    
    # Stream text
    await client.stream_text(call["call_sid"], "Hello! How can I help you?")

asyncio.run(main())
```

### JavaScript WebSocket Example

```javascript
const ws = new WebSocket('ws://your-domain.com/api/v1/webrtc/twilio-elevenlabs-stream/peer123/company-key/agent456');

ws.onopen = function() {
    console.log('Connected to ElevenLabs Twilio stream');
};

ws.onmessage = function(event) {
    const message = JSON.parse(event.data);
    
    switch(message.type) {
        case 'connection_established':
            console.log('Connection established:', message);
            break;
        case 'audio':
            // Handle audio data
            const audioData = atob(message.data);
            playAudio(audioData);
            break;
        case 'synthesis_started':
            console.log('Synthesis started:', message.text);
            break;
        case 'synthesis_error':
            console.error('Synthesis error:', message.error);
            break;
    }
};

// Send text for synthesis
function synthesizeText(text) {
    ws.send(JSON.stringify({
        type: 'text',
        text: text
    }));
}

// Stop synthesis
function stopSynthesis() {
    ws.send(JSON.stringify({
        type: 'control',
        action: 'stop_synthesis'
    }));
}

// Update voice settings
function updateVoice(voiceId, settings) {
    ws.send(JSON.stringify({
        type: 'control',
        action: 'update_voice',
        voice_id: voiceId,
        voice_settings: settings
    }));
}
```

### cURL Examples

#### Health Check
```bash
curl -X GET "https://your-domain.com/api/v1/elevenlabs-twilio/health"
```

#### Get Available Voices
```bash
curl -X GET "https://your-domain.com/api/v1/elevenlabs-twilio/voices"
```

#### Validate Voice ID
```bash
curl -X GET "https://your-domain.com/api/v1/elevenlabs-twilio/voices/IKne3meq5aSn9XLyUdCD/validate"
```

#### Create Integrated Call
```bash
curl -X POST "https://your-domain.com/api/v1/elevenlabs-twilio/calls" \
  -H "Content-Type: application/json" \
  -d '{
    "to_number": "+1234567890",
    "from_number": "+0987654321",
    "webhook_url": "https://your-domain.com/api/v1/elevenlabs-twilio/webhook",
    "voice_id": "IKne3meq5aSn9XLyUdCD",
    "voice_settings": {
      "stability": 0.7,
      "similarity_boost": 0.8,
      "style": 0.1,
      "use_speaker_boost": true
    },
    "metadata": {
      "customer_id": "12345",
      "call_type": "support"
    }
  }'
```

#### Handle Incoming Call
```bash
curl -X POST "https://your-domain.com/api/v1/elevenlabs-twilio/calls/incoming" \
  -H "Content-Type: application/json" \
  -d '{
    "call_sid": "CA1234567890abcdef",
    "from_number": "+1234567890",
    "to_number": "+0987654321",
    "company_api_key": "your-company-key",
    "agent_id": "agent-123",
    "voice_id": "IKne3meq5aSn9XLyUdCD",
    "voice_settings": {
      "stability": 0.8,
      "similarity_boost": 0.9
    }
  }'
```

#### Synthesize Text for Call (REST)
```bash
curl -X POST "https://your-domain.com/api/v1/elevenlabs-twilio/calls/CA1234567890abcdef/synthesize" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello! How can I help you today?",
    "voice_id": "IKne3meq5aSn9XLyUdCD",
    "voice_settings": {
      "stability": 0.8
    }
  }' \
  --output synthesized_audio.mp3
```

#### Stream Text to Call
```bash
curl -X POST "https://your-domain.com/api/v1/elevenlabs-twilio/calls/CA1234567890abcdef/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello! How can I help you today?"
  }'
```

#### Stop Call Synthesis
```bash
curl -X POST "https://your-domain.com/api/v1/elevenlabs-twilio/calls/CA1234567890abcdef/stop"
```

#### Get Call Integration Info
```bash
curl -X GET "https://your-domain.com/api/v1/elevenlabs-twilio/calls/CA1234567890abcdef"
```

#### Update Call Voice Settings
```bash
curl -X POST "https://your-domain.com/api/v1/elevenlabs-twilio/calls/CA1234567890abcdef/voice?voice_id=new-voice-id" \
  -H "Content-Type: application/json" \
  -d '{
    "stability": 0.9,
    "similarity_boost": 0.95
  }'
```

#### End Integrated Call
```bash
curl -X DELETE "https://your-domain.com/api/v1/elevenlabs-twilio/calls/CA1234567890abcdef"
```

#### WebSocket Connection (using wscat or similar tool)
```bash
# Install wscat if not available: npm install -g wscat
wscat -c "ws://your-domain.com/api/v1/webrtc/twilio-elevenlabs-stream/peer123/company-key/agent456"

# Send text for synthesis
echo '{"type": "text", "text": "Hello! How can I help you today?"}' | wscat -c "ws://your-domain.com/api/v1/webrtc/twilio-elevenlabs-stream/peer123/company-key/agent456"

# Send control message to stop synthesis
echo '{"type": "control", "action": "stop_synthesis"}' | wscat -c "ws://your-domain.com/api/v1/webrtc/twilio-elevenlabs-stream/peer123/company-key/agent456"

# Update voice settings
echo '{"type": "control", "action": "update_voice", "voice_id": "new-voice-id", "voice_settings": {"stability": 0.8}}' | wscat -c "ws://your-domain.com/api/v1/webrtc/twilio-elevenlabs-stream/peer123/company-key/agent456"

# Get call info
echo '{"type": "control", "action": "get_call_info"}' | wscat -c "ws://your-domain.com/api/v1/webrtc/twilio-elevenlabs-stream/peer123/company-key/agent456"

# Send ping
echo '{"type": "ping"}' | wscat -c "ws://your-domain.com/api/v1/webrtc/twilio-elevenlabs-stream/peer123/company-key/agent456"
```

## Error Handling

### HTTP Status Codes

- **200**: Success
- **400**: Bad Request (invalid parameters)
- **404**: Not Found (call/voice not found)
- **500**: Internal Server Error
- **503**: Service Unavailable (ElevenLabs/Twilio not configured)

### Common Error Responses

```json
{
  "detail": "ElevenLabs API key not configured. Please set ELEVEN_LABS_API_KEY environment variable."
}
```

```json
{
  "detail": "Invalid voice ID: invalid-voice-id"
}
```

```json
{
  "detail": "Call not found"
}
```

### WebSocket Error Handling

WebSocket connections automatically handle:
- Connection failures
- Reconnection attempts
- Audio streaming errors
- Synthesis failures

## Best Practices

### Voice Selection

1. **Choose appropriate voices** for your use case
2. **Test voice settings** before production use
3. **Consider voice consistency** across your application
4. **Validate voice IDs** before using them

### Call Management

1. **Monitor call status** and handle lifecycle events
2. **Clean up resources** when calls end
3. **Handle errors gracefully** with fallback options
4. **Use appropriate timeouts** for API calls

### Performance Optimization

1. **Use streaming synthesis** for real-time applications
2. **Pre-generate audio** for static content
3. **Cache voice settings** to reduce API calls
4. **Monitor audio quality** and adjust settings accordingly

### Security

1. **Validate all inputs** before processing
2. **Use HTTPS** for all API communications
3. **Implement rate limiting** to prevent abuse
4. **Monitor API usage** and costs

## Troubleshooting

### Common Issues

#### Voice not synthesizing
- Check ElevenLabs API key configuration
- Verify voice ID is valid
- Check network connectivity to ElevenLabs

#### Call not connecting
- Verify Twilio credentials
- Check webhook URL accessibility
- Ensure phone numbers are valid

#### Audio quality issues
- Adjust voice stability settings
- Check audio format compatibility
- Monitor network latency

#### WebSocket disconnections
- Implement automatic reconnection
- Check server load and resources
- Monitor connection timeouts

### Debugging

Enable debug logging by setting the log level:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Monitoring

Monitor the following metrics:
- Call success/failure rates
- Audio synthesis latency
- WebSocket connection stability
- API response times
- Error rates by endpoint

## Support

For issues and questions:

1. Check the troubleshooting section
2. Review error logs and responses
3. Test with the provided examples
4. Contact support with detailed error information

## License

This integration is part of the CSAI Processor project and follows the same licensing terms.
