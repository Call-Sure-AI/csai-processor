"""
Celery Tasks Module
"""
from .base import *
from .email import *
from .file_processing import *
from .ai_processing import *
from .cleanup import *
from .monitoring import *
from .scheduler import *
from .twilio_calls import *

__all__ = [
    # Base tasks
    "BaseTask",
    
    # Email tasks
    "send_email_task",
    "send_bulk_email_task",
    "send_notification_task",
    
    # File processing tasks
    "process_file_task",
    "process_document_task",
    "process_image_task",
    "process_audio_task",
    "process_video_task",
    
    # AI processing tasks
    "process_ai_request_task",
    "generate_embedding_task",
    "process_chat_task",
    "analyze_sentiment_task",
    "extract_text_task",
    
    # Cleanup tasks
    "cleanup_old_tasks",
    "cleanup_old_files",
    "cleanup_old_logs",
    
    # Monitoring tasks
    "health_check",
    "system_stats",
    "queue_stats",
    
    # Scheduler tasks
    "process_pending_tasks",
    "retry_failed_tasks",
    
    # Twilio call tasks
    "queue_outbound_call_task",
    "queue_bulk_calls_task",
    "schedule_call_task",
    "retry_failed_call_task",
    "end_call_task",
    "cleanup_stale_calls_task",
    "get_call_status_task",
]
