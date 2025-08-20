"""
Celery Application Configuration
"""
import os
from celery import Celery
from celery.schedules import crontab
from config.settings import settings

# Create Celery instance
celery_app = Celery(
    "csai_processor",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "services.celery_tasks",
        "services.celery_tasks.twilio_calls"
    ]
)

# Celery Configuration
celery_app.conf.update(
    # Serialization
    task_serializer=settings.celery_task_serializer,
    result_serializer=settings.celery_result_serializer,
    accept_content=settings.celery_accept_content,
    
    # Timezone
    timezone=settings.celery_timezone,
    enable_utc=settings.celery_enable_utc,
    
    # Task Settings
    task_track_started=settings.celery_task_track_started,
    task_time_limit=settings.celery_task_time_limit,
    task_soft_time_limit=settings.celery_task_soft_time_limit,
    task_ignore_result=settings.celery_task_ignore_result,
    task_store_errors_even_if_ignored=settings.celery_task_store_errors_even_if_ignored,
    task_acks_late=settings.celery_task_acks_late,
    task_reject_on_worker_lost=settings.celery_task_reject_on_worker_lost,
    
    # Worker Settings
    worker_prefetch_multiplier=settings.celery_worker_prefetch_multiplier,
    worker_max_tasks_per_child=settings.celery_worker_max_tasks_per_child,
    worker_disable_rate_limits=settings.celery_worker_disable_rate_limits,
    
    # Windows Compatibility
    worker_pool_restarts=True,
    worker_pool="solo", 
    
    # Testing
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=settings.celery_task_eager_propagates,
    
    # Result Backend
    result_expires=3600,  # 1 hour
    result_persistent=True,
    
    # Broker Settings
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=10,
    
    # Task Routing
    task_routes={
        "services.celery_tasks.*": {"queue": "default"},
        "services.celery_tasks.high_priority.*": {"queue": "high_priority"},
        "services.celery_tasks.low_priority.*": {"queue": "low_priority"},
        "services.celery_tasks.email.*": {"queue": "email"},
        "services.celery_tasks.file_processing.*": {"queue": "file_processing"},
        "services.celery_tasks.ai_processing.*": {"queue": "ai_processing"},
        "services.celery_tasks.twilio_calls.*": {"queue": "twilio_calls"},
    },
    
    # Queue Definitions
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
    
    # Beat Schedule (for periodic tasks)
    beat_schedule={
        "cleanup-old-tasks": {
            "task": "services.celery_tasks.cleanup.cleanup_old_tasks",
            "schedule": crontab(hour=2, minute=0),  # Daily at 2 AM
        },
        "health-check": {
            "task": "services.celery_tasks.monitoring.health_check",
            "schedule": 300.0,  # Every 5 minutes
        },
        "process-pending-tasks": {
            "task": "services.celery_tasks.scheduler.process_pending_tasks",
            "schedule": 60.0,  # Every minute
        },
        "cleanup-stale-calls": {
            "task": "services.celery_tasks.twilio_calls.cleanup_stale_calls_task",
            "schedule": crontab(hour=3, minute=0),  # Daily at 3 AM
        },
    },
    
    # Task Annotations (for specific task configurations)
    task_annotations={
        "services.celery_tasks.*": {
            "rate_limit": "100/m",  # 100 tasks per minute
        },
        "services.celery_tasks.high_priority.*": {
            "rate_limit": "200/m",  # 200 tasks per minute
        },
        "services.celery_tasks.ai_processing.*": {
            "rate_limit": "10/m",  # 10 tasks per minute (AI tasks are resource-intensive)
            "time_limit": 600,  # 10 minutes
            "soft_time_limit": 540,  # 9 minutes
        },
        "services.celery_tasks.file_processing.*": {
            "rate_limit": "50/m",  # 50 tasks per minute
            "time_limit": 1800,  # 30 minutes
            "soft_time_limit": 1620,  # 27 minutes
        },
        "services.celery_tasks.twilio_calls.*": {
            "rate_limit": "30/m",  # 30 calls per minute (Twilio rate limits)
            "time_limit": 300,  # 5 minutes
            "soft_time_limit": 270,  # 4.5 minutes
        },
    },
)

# Auto-discover tasks in the project
celery_app.autodiscover_tasks()

if __name__ == "__main__":
    celery_app.start()
