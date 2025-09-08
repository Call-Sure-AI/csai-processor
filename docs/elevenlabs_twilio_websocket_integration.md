# ElevenLabs Twilio WebSocket Integration

This document explains how to use the ElevenLabs-Twilio WebSocket integration for real-time voice calls.

## Overview

The integration allows you to:
- Receive incoming Twilio calls
- Use ElevenLabs text-to-speech for voice synthesis
- Stream audio in real-time via WebSocket connections
- Handle call events and audio processing

## Architecture

```
Twilio Call → Webhook → TwiML Response → WebSocket Connection → ElevenLabs TTS → Audio Stream
```

## Setup

### 1. Environment Variables

Make sure you have the following environment variables set:

```bash
# ElevenLabs Configuration
ELEVEN_LABS_API_KEY=your_elevenlabs_api_key_here
VOICE_ID=21m00Tcm4TlvDq8ikWAM  # Optional, defaults to this voice

# Server Configuration
BASE_URL=https://your-domain.com  # Your public server URL
WEBHOOK_BASE_URL=https://your-domain.com  # For webhooks

# Twilio Configuration (if using Twilio service)
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_PHONE_NUMBER=your_twilio_phone_number
```

### 2. Install Dependencies

The integration requires the following Python packages:
- `fastapi`
- `websockets`
- `httpx`
- `twilio`
- `pydantic`

## API Endpoints

### 1. Webhook Endpoint

**POST** `/api/v1/elevenlabs-twilio/call/incoming`

This endpoint receives incoming call webhooks from Twilio and returns TwiML to establish a WebSocket connection.

**Request Format:**
```
Form Data:
- CallSid: Twilio call SID
- From: Caller phone number
- To: Called phone number
- CallStatus: Call status
```

**Response:**
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://your-domain.com/api/v1/elevenlabs-twilio/call/connection" />
    </Connect>
</Response>
```

### 2. WebSocket Endpoint

**WebSocket** `/api/v1/elevenlabs-twilio/call/connection`

This endpoint handles the WebSocket connection for real-time audio streaming.

**Message Format:**

**From Twilio (incoming):**
```json
{
    "event": "start",
    "start": {
        "callSid": "CA1234567890abcdef",
        "streamSid": "MZ1234567890abcdef"
    }
}
```

**To Twilio (outgoing):**
```json
{
    "streamSid": "MZ1234567890abcdef",
    "event": "media",
    "media": {
        "payload": "base64_encoded_audio_data"
    }
}
```

### 3. Test Endpoints

**GET** `/api/v1/elevenlabs-twilio/test`
- Health check endpoint

**GET** `/api/v1/elevenlabs-twilio/connections`
- List active WebSocket connections

**POST** `/api/v1/elevenlabs-twilio/test-synthesis`
- Test text-to-speech synthesis

## Usage Examples

### 1. Basic Webhook Setup

Configure your Twilio phone number to call this webhook URL:
```
https://your-domain.com/api/v1/elevenlabs-twilio/call/incoming
```

### 2. Custom Voice ID

You can specify a custom voice ID by adding it as a query parameter:
```
https://your-domain.com/api/v1/elevenlabs-twilio/call/incoming?voice_id=21m00Tcm4TlvDq8ikWAM
```

### 3. Testing the Integration

#### Test WebSocket Connection

```python
import asyncio
import json
import websockets

async def test_websocket():
    uri = "ws://localhost:8001/api/v1/elevenlabs-twilio/call/connection"
    
    async with websockets.connect(uri) as websocket:
        # Send start message
        start_message = {
            "event": "start",
            "start": {
                "callSid": "test_call_123",
                "streamSid": "test_stream_456"
            }
        }
        
        await websocket.send(json.dumps(start_message))
        
        # Wait for response
        response = await websocket.recv()
        print(f"Received: {response}")

asyncio.run(test_websocket())
```

#### Test Webhook

```python
import requests

# Test webhook endpoint
response = requests.post(
    "http://localhost:8001/api/v1/elevenlabs-twilio/call/incoming",
    data={
        "CallSid": "test_call_123",
        "From": "+1234567890",
        "To": "+0987654321"
    }
)

print(f"TwiML Response: {response.text}")
```

## Configuration

### Voice Settings

The integration uses the following default voice settings:

```json
{
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": true
}
```

### Audio Format

- **Input Format**: Text (UTF-8)
- **Output Format**: μ-law 8000 Hz (Twilio-compatible)
- **Encoding**: Base64 for WebSocket transmission

## Error Handling

The integration includes comprehensive error handling:

1. **WebSocket Connection Errors**: Automatic reconnection attempts
2. **ElevenLabs API Errors**: Fallback to default voice or error messages
3. **Twilio Webhook Errors**: Graceful degradation with fallback TwiML

## Monitoring

### Logs

The integration logs all important events:
- WebSocket connections/disconnections
- Call start/end events
- Audio synthesis status
- Error conditions

### Metrics

Available metrics:
- Active WebSocket connections
- Call success/failure rates
- Audio synthesis latency
- Error rates by type

## Troubleshooting

### Common Issues

1. **WebSocket Connection Fails**
   - Check if the server is running
   - Verify the WebSocket URL is correct
   - Check firewall settings

2. **No Audio in Call**
   - Verify ElevenLabs API key is set
   - Check voice ID is valid
   - Monitor logs for synthesis errors

3. **Webhook Not Called**
   - Verify Twilio phone number configuration
   - Check webhook URL is publicly accessible
   - Test webhook endpoint manually

### Debug Mode

Enable debug logging by setting:
```bash
LOG_LEVEL=DEBUG
```

## Security Considerations

1. **Webhook Validation**: Implement Twilio signature validation in production
2. **Rate Limiting**: Add rate limiting to prevent abuse
3. **Authentication**: Consider adding API key authentication for webhook endpoints
4. **HTTPS/WSS**: Always use secure connections in production

## Performance Optimization

1. **Connection Pooling**: Reuse HTTP connections for ElevenLabs API calls
2. **Audio Caching**: Cache frequently used audio snippets
3. **Load Balancing**: Use multiple server instances for high availability
4. **Monitoring**: Implement proper monitoring and alerting

## Example Implementation

See the test files:
- `test_elevenlabs_websocket.py` - WebSocket testing
- `test_twilio_webhook.py` - Webhook testing

## Support

For issues or questions:
1. Check the logs for error messages
2. Verify configuration settings
3. Test individual components separately
4. Review the ElevenLabs and Twilio documentation
