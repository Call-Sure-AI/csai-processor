# Celery Twilio API Documentation

## Overview

The Celery Twilio API provides RESTful endpoints for queuing Twilio calls through Celery. This allows you to make outbound calls, schedule calls, and manage call campaigns asynchronously with proper rate limiting and error handling.

## Base URL

```
http://localhost:8001/api/v1/celery/twilio
```

## Authentication

Currently, the API doesn't require authentication. In production, you should implement proper authentication and authorization.

## Endpoints

### 1. Health Check

**GET** `/health`

Check if the Celery service and Twilio configuration are working properly.

#### Response

```json
{
  "status": "healthy",
  "twilio_configured": true,
  "celery_configured": true,
  "timestamp": "2024-01-15T10:00:00Z"
}
```

#### Example

```bash
curl -X GET "http://localhost:8001/api/v1/celery/twilio/health"
```

### 2. Queue Single Call

**POST** `/queue-single-call`

Queue a single outbound call to be processed asynchronously by the Celery worker.

#### Request Body

```json
{
  "to_number": "+1234567890",
  "from_number": "+1987654321",
  "webhook_url": "https://your-domain.com/api/v1/twilio/incoming-call",
  "status_callback_url": "https://your-domain.com/api/v1/twilio/call-status",
  "call_metadata": {
    "customer_id": "12345",
    "call_type": "follow_up",
    "agent_id": "agent_001",
    "priority": "normal",
    "custom_data": {
      "campaign": "winter_2024"
    }
  },
  "delay_seconds": 10
}
```

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `to_number` | string | Yes | Phone number to call (E.164 format) |
| `from_number` | string | No | Phone number to call from (uses default if not provided) |
| `webhook_url` | string | No | Custom webhook URL for call handling |
| `status_callback_url` | string | No | Custom status callback URL |
| `call_metadata` | object | No | Call metadata for tracking |
| `delay_seconds` | integer | No | Delay before making call (0-3600 seconds) |

#### Response

```json
{
  "success": true,
  "task_id": "abc123-def456-ghi789",
  "message": "Call to +1234567890 queued successfully",
  "created_at": "2024-01-15T10:00:00Z",
  "estimated_completion": "2024-01-15T10:01:10Z"
}
```

#### Example

```bash
curl -X POST "http://localhost:8001/api/v1/celery/twilio/queue-single-call" \
  -H "Content-Type: application/json" \
  -d '{
    "to_number": "+1234567890",
    "call_metadata": {
      "customer_id": "12345",
      "call_type": "test_call",
      "message": "This is a test call from Celery Twilio API",
      "voice": "alice",
      "language": "en-US",
    },
    "delay_seconds": 10
  }'
```

### 3. Queue Bulk Calls

**POST** `/queue-bulk-calls`

Queue multiple outbound calls with rate limiting to respect Twilio's API limits.

#### Request Body

```json
{
  "phone_numbers": [
    "+1234567890",
    "+1234567891",
    "+1234567892",
    "+1234567893"
  ],
  "from_number": "+1987654321",
  "webhook_url": "https://your-domain.com/api/v1/twilio/incoming-call",
  "status_callback_url": "https://your-domain.com/api/v1/twilio/call-status",
  "call_metadata": {
    "campaign_id": "campaign_001",
    "call_type": "survey",
    "agent_id": "agent_002",
    "priority": "normal"
  },
  "delay_between_calls": 30,
  "max_concurrent_calls": 3
}
```

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `phone_numbers` | array | Yes | List of phone numbers to call (1-1000 numbers) |
| `from_number` | string | No | Phone number to call from |
| `webhook_url` | string | No | Custom webhook URL for call handling |
| `status_callback_url` | string | No | Custom status callback URL |
| `call_metadata` | object | No | Call metadata for all calls |
| `delay_between_calls` | integer | No | Seconds between calls (1-300) |
| `max_concurrent_calls` | integer | No | Maximum concurrent calls (1-50) |

#### Response

```json
{
  "success": true,
  "task_id": "abc123-def456-ghi789",
  "message": "Bulk call campaign for 4 numbers queued successfully",
  "total_calls": 4,
  "estimated_duration_minutes": 2,
  "created_at": "2024-01-15T10:00:00Z"
}
```

#### Example

```bash
curl -X POST "http://localhost:8001/api/v1/celery/twilio/queue-bulk-calls" \
  -H "Content-Type: application/json" \
  -d '{
    "phone_numbers": ["+1234567890", "+1234567891"],
    "call_metadata": {
      "campaign_id": "test_campaign",
      "call_type": "survey"
    },
    "delay_between_calls": 30
  }'
```

### 4. Schedule Call

**POST** `/schedule-call`

Schedule a call for a specific time using Celery's scheduling capabilities.

#### Request Body

```json
{
  "to_number": "+1234567890",
  "from_number": "+1987654321",
  "webhook_url": "https://your-domain.com/api/v1/twilio/incoming-call",
  "status_callback_url": "https://your-domain.com/api/v1/twilio/call-status",
  "call_metadata": {
    "customer_id": "67890",
    "call_type": "appointment_reminder",
    "agent_id": "agent_003"
  },
  "schedule_time": "2024-01-15T14:00:00Z",
  "timezone": "Asia/Kolkata"
}
```

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `to_number` | string | Yes | Phone number to call (E.164 format) |
| `from_number` | string | No | Phone number to call from |
| `webhook_url` | string | No | Custom webhook URL for call handling |
| `status_callback_url` | string | No | Custom status callback URL |
| `call_metadata` | object | No | Call metadata |
| `schedule_time` | string | Yes | ISO format datetime when to make the call |
| `timezone` | string | No | Timezone for the schedule time (default: America/New_York) |

#### Response

```json
{
  "success": true,
  "task_id": "abc123-def456-ghi789",
  "message": "Call to +1234567890 scheduled for 2024-01-15T14:00:00Z",
  "created_at": "2024-01-15T10:00:00Z",
  "estimated_completion": "2024-01-15T14:00:00Z"
}
```

#### Example

```bash
curl -X POST "http://localhost:8001/api/v1/celery/twilio/schedule-call" \
  -H "Content-Type: application/json" \
  -d '{
    "to_number": "+1234567890",
    "schedule_time": "2024-01-15T14:00:00Z",
    "call_metadata": {
      "customer_id": "67890",
      "call_type": "reminder"
    }
  }'
```

### 5. Get Task Status

**GET** `/task-status/{task_id}`

Get the current status and progress of a queued call task.

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | string | Yes | Celery task ID |

#### Response

```json
{
  "task_id": "abc123-def456-ghi789",
  "status": "PROGRESS",
  "progress": 50,
  "message": "Creating call",
  "result": null,
  "error": null,
  "created_at": "2024-01-15T10:00:00Z",
  "updated_at": "2024-01-15T10:00:30Z"
}
```

#### Status Values

- `PENDING`: Task is queued but not yet started
- `PROGRESS`: Task is currently running
- `SUCCESS`: Task completed successfully
- `FAILURE`: Task failed with an error
- `REVOKED`: Task was cancelled

#### Example

```bash
curl -X GET "http://localhost:8001/api/v1/celery/twilio/task-status/abc123-def456-ghi789"
```

### 6. Cancel Task

**DELETE** `/cancel-task/{task_id}`

Cancel a pending or running call task.

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task_id` | string | Yes | Celery task ID |

#### Response

```json
{
  "success": true,
  "message": "Task abc123-def456-ghi789 cancelled successfully",
  "task_id": "abc123-def456-ghi789"
}
```

#### Example

```bash
curl -X DELETE "http://localhost:8001/api/v1/celery/twilio/cancel-task/abc123-def456-ghi789"
```

## Error Handling

### HTTP Status Codes

- `200 OK`: Request successful
- `400 Bad Request`: Invalid request parameters
- `404 Not Found`: Task not found
- `500 Internal Server Error`: Server error

### Error Response Format

```json
{
  "detail": "Error message describing what went wrong"
}
```

### Common Error Scenarios

1. **Invalid Phone Number Format**
   ```json
   {
     "detail": "Phone number must be in E.164 format (e.g., +1234567890)"
   }
   ```

2. **Task Not Found**
   ```json
   {
     "detail": "Task abc123-def456-ghi789 not found or could not be cancelled"
   }
   ```

3. **Twilio Configuration Missing**
   ```json
   {
     "detail": "Failed to queue call: Twilio service not initialized"
   }
   ```

## Rate Limiting

The API respects Twilio's rate limits:
- Single calls: Up to 30 calls per minute
- Bulk calls: Configurable delay between calls (1-300 seconds)
- Maximum concurrent calls: Configurable (1-50)

## Call Metadata

The `call_metadata` object allows you to track and organize your calls:

```json
{
  "customer_id": "12345",
  "call_type": "follow_up",
  "agent_id": "agent_001",
  "campaign_id": "campaign_001",
  "priority": "normal",
  "custom_data": {
    "source": "web_form",
    "product": "premium_plan",
    "language": "en"
  }
}
```

## Webhook Integration

You can specify custom webhook URLs for call handling:

- `webhook_url`: URL that Twilio will call when the call connects
- `status_callback_url`: URL that Twilio will call with call status updates

## Testing

### Using the Test Script

Run the provided test script to verify the API endpoints:

```bash
python src/examples/test_celery_twilio_api.py
```

### Manual Testing with curl

1. **Health Check**
   ```bash
   curl -X GET "http://localhost:8001/api/v1/celery/twilio/health"
   ```

2. **Queue Single Call**
   ```bash
   curl -X POST "http://localhost:8001/api/v1/celery/twilio/queue-single-call" \
     -H "Content-Type: application/json" \
     -d '{"to_number": "+1234567890", "delay_seconds": 10}'
   ```

3. **Queue Bulk Calls**
   ```bash
   curl -X POST "http://localhost:8001/api/v1/celery/twilio/queue-bulk-calls" \
     -H "Content-Type: application/json" \
     -d '{"phone_numbers": ["+1234567890", "+1234567891"]}'
   ```

## Prerequisites

1. **FastAPI Server**: Make sure the FastAPI server is running
2. **Celery Workers**: Start Celery workers with the `twilio_calls` queue
3. **Redis**: Ensure Redis is running for Celery broker/backend
4. **Twilio Configuration**: Set up Twilio credentials in environment variables

### Starting Required Services

```bash
# Start FastAPI server
uvicorn src.app:app --reload --host 0.0.0.0 --port 8001

# Start Celery worker
python scripts/start_celery.py worker --queues twilio_calls

# Start Celery beat (for scheduled calls)
python scripts/start_celery.py beat

# Start Flower monitoring (optional)
python scripts/start_celery.py flower
```

## Environment Variables

Make sure these environment variables are set:

```bash
# Twilio Configuration
TWILIO_ACCOUNT_SID=your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_PHONE_NUMBER=your_twilio_number

# Celery Configuration
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

## Monitoring

### Flower Web Interface

Access Flower at `http://localhost:5555` to monitor:
- Task execution status
- Worker health
- Queue statistics
- Task history

### API Monitoring

Use the `/task-status/{task_id}` endpoint to monitor individual tasks programmatically.

## Best Practices

1. **Phone Number Validation**: Always use E.164 format (+1234567890)
2. **Rate Limiting**: Use appropriate delays between bulk calls
3. **Error Handling**: Always check task status for failures
4. **Metadata**: Use call metadata for tracking and analytics
5. **Webhooks**: Implement proper webhook handling for call events
6. **Monitoring**: Monitor task status and worker health regularly

## Troubleshooting

### Common Issues

1. **Tasks Not Processing**
   - Check if Celery workers are running
   - Verify Redis connection
   - Check worker queue configuration

2. **Twilio Errors**
   - Verify Twilio credentials
   - Check phone number format
   - Ensure Twilio account has sufficient credits

3. **API Connection Issues**
   - Verify FastAPI server is running
   - Check CORS configuration
   - Ensure proper request format

### Debug Mode

Enable debug logging by setting the log level:

```bash
export LOG_LEVEL=DEBUG
```

## Support

For issues and questions:
1. Check the logs for error messages
2. Verify all prerequisites are met
3. Test with the provided test script
4. Monitor task status through the API
