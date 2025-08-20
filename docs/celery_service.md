# Celery Service Documentation

## Overview

This Celery service provides a comprehensive, optimized, and manageable task queue system for the CSAI Processor. It includes various task types, monitoring capabilities, and a clean service interface.

## Features

- **Multiple Task Types**: Email, file processing, AI processing, cleanup, monitoring, and scheduling
- **Queue Management**: Separate queues for different task types with priority handling
- **Task Monitoring**: Progress tracking, status monitoring, and result retrieval
- **Automatic Retries**: Configurable retry logic with exponential backoff
- **Health Monitoring**: System health checks and performance monitoring
- **Cleanup Tasks**: Automatic cleanup of old tasks, files, and logs
- **Scheduling**: Periodic task execution with Celery Beat
- **Flower Integration**: Web-based monitoring and management interface

## Architecture

### Components

1. **Celery App** (`services/celery_app.py`): Main Celery application configuration
2. **Base Task** (`services/celery_tasks/base.py`): Base task class with common functionality
3. **Task Modules**: Specialized task modules for different categories
4. **Service Manager** (`services/celery_service.py`): Clean interface for using tasks
5. **Startup Scripts**: Scripts for running workers, beat, and flower

### Queues

- `default`: General tasks
- `high_priority`: High priority tasks
- `low_priority`: Low priority tasks
- `email`: Email-related tasks
- `file_processing`: File processing tasks
- `ai_processing`: AI/ML processing tasks
- `twilio_calls`: Twilio call queuing tasks

## Configuration

### Environment Variables

Add these to your `.env` file:

```bash
# Celery Configuration
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
CELERY_WORKER_CONCURRENCY=4
CELERY_WORKER_MAX_TASKS_PER_CHILD=1000
CELERY_TASK_TIME_LIMIT=1800
CELERY_TASK_SOFT_TIME_LIMIT=1620
```

### Settings

The service uses the existing settings system. Key Celery settings:

```python
# From config/settings.py
celery_broker_url: str = "redis://localhost:6379/0"
celery_result_backend: str = "redis://localhost:6379/0"
celery_worker_concurrency: int = 4
celery_task_time_limit: int = 30 * 60  # 30 minutes
celery_task_soft_time_limit: int = 25 * 60  # 25 minutes
```

## Usage

### Basic Usage

```python
from services.celery_service import celery_service

# Send an email
task_id = celery_service.send_email(
    to_email="user@example.com",
    subject="Test Email",
    body="Hello from Celery!"
)

# Process a file
task_id = celery_service.process_document(
    file_path="/path/to/document.pdf",
    extract_text=True,
    generate_summary=True
)

# Generate AI embedding
task_id = celery_service.generate_embedding(
    text="Sample text for embedding",
    model="text-embedding-ada-002"
)
```

### Task Monitoring

```python
# Get task status
status = celery_service.get_task_status(task_id)
print(f"Task status: {status['status']}")

# Wait for result
result = celery_service.get_task_result(task_id, timeout=60)
print(f"Task result: {result}")

# Cancel task
success = celery_service.cancel_task(task_id)
```

### Email Tasks

```python
# Send single email
task_id = celery_service.send_email(
    to_email="user@example.com",
    subject="Welcome",
    body="Welcome to our platform!",
    priority="high"
)

# Send bulk emails
emails = [
    {"to_email": "user1@example.com", "subject": "Newsletter", "body": "Content"},
    {"to_email": "user2@example.com", "subject": "Newsletter", "body": "Content"},
]
task_id = celery_service.send_bulk_emails(emails, batch_size=50)

# Send notification
task_id = celery_service.send_notification(
    user_id="12345",
    notification_type="welcome",
    title="Welcome!",
    message="Welcome to our platform!",
    channels=["email", "push"]
)
```

### File Processing Tasks

```python
# Process document
task_id = celery_service.process_document(
    file_path="/path/to/document.pdf",
    extract_text=True,
    generate_summary=True,
    extract_metadata=True,
    ocr_enabled=True
)

# Process image
task_id = celery_service.process_image(
    file_path="/path/to/image.jpg",
    extract_text=True,
    detect_objects=True,
    face_recognition=True,
    generate_tags=True
)

# Process audio
task_id = celery_service.process_audio(
    file_path="/path/to/audio.mp3",
    transcribe=True,
    detect_speakers=True,
    extract_features=True,
    language="en"
)

# Process video
task_id = celery_service.process_video(
    file_path="/path/to/video.mp4",
    extract_frames=True,
    process_audio=True,
    detect_scenes=True,
    generate_thumbnail=True
)
```

### AI Processing Tasks

```python
# Generate embedding
task_id = celery_service.generate_embedding(
    text="Sample text for embedding",
    model="text-embedding-ada-002"
)

# Process chat
task_id = celery_service.process_chat(
    message="Hello, how are you?",
    context=[
        {"role": "user", "content": "Previous message"},
        {"role": "assistant", "content": "Previous response"}
    ],
    model="gpt-4"
)

# Analyze sentiment
task_id = celery_service.analyze_sentiment(
    text="I love this product!",
    detailed=True
)

# Extract text
task_id = celery_service.extract_text(
    content="Content to extract text from",
    content_type="text",
    language="en"
)
```

### Monitoring Tasks

```python
# Health check
task_id = celery_service.health_check()

# System statistics
task_id = celery_service.get_system_stats()

# Queue statistics
task_id = celery_service.get_queue_stats()

# Worker statistics
worker_stats = celery_service.get_worker_stats()
```

### Cleanup Tasks

```python
# Cleanup old tasks
task_id = celery_service.cleanup_old_tasks(max_age_hours=24)

# Cleanup old files
task_id = celery_service.cleanup_old_files(
    directory="/tmp/old_files",
    max_age_hours=168,  # 7 days
    dry_run=True  # Dry run first
)

# Cleanup old logs
task_id = celery_service.cleanup_old_logs(
    log_directory="logs",
    max_age_hours=168
)
```

### Scheduler Tasks

```python
# Process pending tasks
task_id = celery_service.process_pending_tasks(
    max_tasks=100,
    task_types=["email", "file_processing"]
)

# Retry failed tasks
task_id = celery_service.retry_failed_tasks(
    max_retries=3,
    max_age_hours=24,
    task_types=["email"]
)
```

### Twilio Call Queuing

The Celery service includes comprehensive Twilio call queuing capabilities for managing outbound calls, bulk calling campaigns, and call scheduling.

#### Prerequisites

1. **Twilio Account**: Set up your Twilio credentials in environment variables:
   ```bash
   TWILIO_ACCOUNT_SID=your_account_sid
   TWILIO_AUTH_TOKEN=your_auth_token
   TWILIO_PHONE_NUMBER=your_twilio_number
   ```

2. **Twilio Service**: The service integrates with your existing `TwilioVoiceService`

#### Single Call Queuing

```python
# Queue a single outbound call
task_id = celery_service.queue_outbound_call(
    to_number="+1234567890",
    from_number="+1987654321",  # Optional, uses default if not provided
    call_metadata={
        "customer_id": "12345",
        "call_type": "follow_up",
        "agent_id": "agent_001"
    },
    delay_seconds=10  # Optional delay before making the call
)

# Monitor the call task
status = celery_service.get_task_status(task_id)
result = celery_service.get_task_result(task_id)
```

#### Bulk Call Campaigns

```python
# Queue multiple calls with rate limiting
phone_numbers = [
    "+1234567890",
    "+1234567891", 
    "+1234567892",
    "+1234567893"
]

task_id = celery_service.queue_bulk_calls(
    phone_numbers=phone_numbers,
    from_number="+1987654321",
    call_metadata={
        "campaign_id": "campaign_001",
        "call_type": "survey",
        "agent_id": "agent_002"
    },
    delay_between_calls=30,  # 30 seconds between calls
    max_concurrent_calls=3   # Max 3 concurrent calls
)
```

#### Scheduled Calls

```python
from datetime import datetime, timedelta

# Schedule a call for a specific time
schedule_time = (datetime.utcnow() + timedelta(hours=2)).isoformat()

task_id = celery_service.schedule_call(
    to_number="+1234567890",
    schedule_time=schedule_time,
    call_metadata={
        "customer_id": "67890",
        "call_type": "appointment_reminder"
    }
)
```

#### Call Management

```python
# End an active call
end_task_id = celery_service.end_call(call_sid="CA1234567890abcdef")

# Get call status
status_task_id = celery_service.get_call_status(call_sid="CA1234567890abcdef")

# Retry a failed call
retry_task_id = celery_service.retry_failed_call(
    original_task_id="failed_task_123",
    to_number="+1234567890",
    retry_delay=300  # 5 minutes delay
)
```

#### Cleanup Operations

```python
# Clean up stale call records (older than 24 hours)
cleanup_task_id = celery_service.cleanup_stale_calls(max_age_hours=24)
```

#### Advanced Call Configuration

```python
# Custom webhook URLs and call parameters
task_id = celery_service.queue_outbound_call(
    to_number="+1234567890",
    webhook_url="https://your-domain.com/api/v1/twilio/incoming-call",
    status_callback_url="https://your-domain.com/api/v1/twilio/call-status",
    call_metadata={
        "custom_param": "value",
        "priority": "high"
    }
)
```

#### Call Task Monitoring

```python
# Monitor call task progress
status = celery_service.get_task_status(task_id)

if status['status'] == 'SUCCESS':
    result = celery_service.get_task_result(task_id)
    call_sid = result.get('call_sid')
    print(f"Call created successfully: {call_sid}")
    
    # Get detailed call status
    call_status = celery_service.get_call_status(call_sid)
    call_result = celery_service.get_task_result(call_status)
    print(f"Call status: {call_result.get('call_info', {}).get('status')}")
```

## Running the Service

### Prerequisites

1. **Redis**: Make sure Redis is running
2. **Dependencies**: Install required packages from `requirements.txt`

### Starting Components

#### Using the Startup Script

```bash
# Start worker
python scripts/start_celery.py worker --queues default email file_processing ai_processing

# Start beat scheduler
python scripts/start_celery.py beat

# Start flower monitoring
python scripts/start_celery.py flower

# Start all components
python scripts/start_celery.py all
```

#### Manual Commands

```bash
# Start worker
celery -A services.celery_app:celery_app worker --loglevel=INFO --concurrency=4

# Start beat scheduler
celery -A services.celery_app:celery_app beat --loglevel=INFO

# Start flower monitoring
celery -A services.celery_app:celery_app flower --port=5555
```

### Worker Configuration

```bash
# Start worker with specific queues
celery -A services.celery_app:celery_app worker \
    --queues=default,email,file_processing,ai_processing \
    --concurrency=4 \
    --loglevel=INFO

# Start worker for specific queue only
celery -A services.celery_app:celery_app worker \
    --queues=email \
    --concurrency=2 \
    --loglevel=INFO
```

## Monitoring

### Flower Web Interface

Access Flower at `http://localhost:5555` for:
- Real-time task monitoring
- Worker status
- Queue statistics
- Task history
- Performance metrics

### Programmatic Monitoring

```python
# Get worker statistics
worker_stats = celery_service.get_worker_stats()

# Get queue statistics
queue_stats = celery_service.get_queue_stats()

# Health check
health_result = celery_service.health_check()
```

## Task Configuration

### Rate Limiting

Tasks are configured with rate limits:
- Default tasks: 100/minute
- High priority tasks: 200/minute
- AI processing tasks: 10/minute (resource-intensive)
- File processing tasks: 50/minute

### Retry Configuration

Default retry settings:
- Max retries: 3
- Exponential backoff: Yes
- Max retry delay: 10 minutes
- Jitter: Yes

### Time Limits

- Default task time limit: 30 minutes
- Soft time limit: 25 minutes
- AI processing tasks: 10 minutes
- File processing tasks: 30 minutes

## Error Handling

### Task Failures

Tasks automatically retry on failure with exponential backoff. Failed tasks can be:
- Monitored via Flower
- Retried manually using `retry_failed_tasks`
- Cleaned up using `cleanup_old_tasks`

### Monitoring Failures

```python
# Check task status
status = celery_service.get_task_status(task_id)
if status['status'] == 'FAILURE':
    print(f"Task failed: {status['traceback']}")

# Retry failed tasks
task_id = celery_service.retry_failed_tasks(
    max_retries=3,
    max_age_hours=24
)
```

## Best Practices

### 1. Queue Selection

- Use appropriate queues for different task types
- High priority tasks should use `high_priority` queue
- Resource-intensive tasks should use dedicated queues

### 2. Task Design

- Keep tasks idempotent
- Use appropriate time limits
- Handle exceptions gracefully
- Update task progress for long-running tasks

### 3. Monitoring

- Monitor worker health regularly
- Set up alerts for failed tasks
- Use Flower for real-time monitoring
- Clean up old tasks and results

### 4. Performance

- Adjust concurrency based on system resources
- Use appropriate rate limits
- Monitor memory usage
- Scale workers as needed

## Troubleshooting

### Common Issues

1. **Redis Connection**: Ensure Redis is running and accessible
2. **Worker Not Starting**: Check Python path and dependencies
3. **Tasks Not Processing**: Verify worker is listening to correct queues
4. **Memory Issues**: Reduce concurrency or increase memory limits

### Debug Mode

```bash
# Start worker in debug mode
celery -A services.celery_app:celery_app worker --loglevel=DEBUG

# Start with specific queues
celery -A services.celery_app:celery_app worker \
    --queues=default \
    --loglevel=DEBUG \
    --concurrency=1
```

### Logs

Check logs for:
- Task execution details
- Error messages
- Performance metrics
- Worker status

## Examples

See `src/examples/celery_example.py` for comprehensive usage examples.

## API Reference

### CeleryService Methods

#### Email Methods
- `send_email()`: Send single email
- `send_bulk_emails()`: Send bulk emails
- `send_notification()`: Send notification

#### File Processing Methods
- `process_file()`: Generic file processing
- `process_document()`: Document processing
- `process_image()`: Image processing
- `process_audio()`: Audio processing
- `process_video()`: Video processing

#### AI Processing Methods
- `process_ai_request()`: Generic AI request
- `generate_embedding()`: Generate embeddings
- `process_chat()`: Process chat messages
- `analyze_sentiment()`: Analyze sentiment
- `extract_text()`: Extract text

#### Monitoring Methods
- `health_check()`: System health check
- `get_system_stats()`: System statistics
- `get_queue_stats()`: Queue statistics
- `get_worker_stats()`: Worker statistics

#### Cleanup Methods
- `cleanup_old_tasks()`: Cleanup old tasks
- `cleanup_old_files()`: Cleanup old files
- `cleanup_old_logs()`: Cleanup old logs

#### Scheduler Methods
- `process_pending_tasks()`: Process pending tasks
- `retry_failed_tasks()`: Retry failed tasks

#### Twilio Call Methods
- `queue_outbound_call()`: Queue single outbound call
- `queue_bulk_calls()`: Queue multiple calls with rate limiting
- `schedule_call()`: Schedule call for specific time
- `retry_failed_call()`: Retry failed call
- `end_call()`: End active call
- `cleanup_stale_calls()`: Clean up stale call records
- `get_call_status()`: Get call status

#### Utility Methods
- `get_task_result()`: Get task result
- `get_task_status()`: Get task status
- `cancel_task()`: Cancel task
- `purge_queue()`: Purge queue
