"""
Celery Service Manager
"""
import time
from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timedelta
from loguru import logger
from .celery_app import celery_app
from .celery_tasks import (
    # Email tasks
    send_email_task,
    send_bulk_email_task,
    send_notification_task,
    
    # File processing tasks
    process_file_task,
    process_document_task,
    process_image_task,
    process_audio_task,
    process_video_task,
    
    # AI processing tasks
    process_ai_request_task,
    generate_embedding_task,
    process_chat_task,
    analyze_sentiment_task,
    extract_text_task,
    
    # Cleanup tasks
    cleanup_old_tasks,
    cleanup_old_files,
    cleanup_old_logs,
    
    # Monitoring tasks
    health_check,
    system_stats,
    queue_stats,
    
    # Scheduler tasks
    process_pending_tasks,
    retry_failed_tasks,
    
    # Twilio call tasks
    queue_outbound_call_task,
    queue_bulk_calls_task,
    schedule_call_task,
    retry_failed_call_task,
    end_call_task,
    cleanup_stale_calls_task,
    get_call_status_task,
)


class CeleryService:
    """
    Service manager for Celery tasks
    """
    
    def __init__(self):
        self.app = celery_app
    
    # Email Methods
    def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        from_email: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        priority: str = "normal",
        queue: str = "email"
    ) -> str:
        """Send a single email"""
        result = send_email_task.apply_async(
            kwargs={
                "to_email": to_email,
                "subject": subject,
                "body": body,
                "from_email": from_email,
                "cc": cc,
                "bcc": bcc,
                "priority": priority
            },
            queue=queue
        )
        return result.id
    
    def send_bulk_emails(
        self,
        emails: List[Dict[str, Any]],
        batch_size: int = 50,
        delay_between_batches: float = 1.0,
        queue: str = "email"
    ) -> str:
        """Send bulk emails"""
        result = send_bulk_email_task.apply_async(
            kwargs={
                "emails": emails,
                "batch_size": batch_size,
                "delay_between_batches": delay_between_batches
            },
            queue=queue
        )
        return result.id
    
    def send_notification(
        self,
        user_id: str,
        notification_type: str,
        title: str,
        message: str,
        channels: Optional[List[str]] = None,
        queue: str = "default"
    ) -> str:
        """Send notification to user"""
        result = send_notification_task.apply_async(
            kwargs={
                "user_id": user_id,
                "notification_type": notification_type,
                "title": title,
                "message": message,
                "channels": channels or ["email"]
            },
            queue=queue
        )
        return result.id
    
    # File Processing Methods
    def process_file(
        self,
        file_path: str,
        file_type: str,
        processing_options: Optional[Dict[str, Any]] = None,
        queue: str = "file_processing"
    ) -> str:
        """Process a file"""
        result = process_file_task.apply_async(
            kwargs={
                "file_path": file_path,
                "file_type": file_type,
                "processing_options": processing_options or {}
            },
            queue=queue
        )
        return result.id
    
    def process_document(
        self,
        file_path: str,
        extract_text: bool = True,
        generate_summary: bool = True,
        queue: str = "file_processing"
    ) -> str:
        """Process a document file"""
        result = process_document_task.apply_async(
            kwargs={
                "file_path": file_path,
                "extract_text": extract_text,
                "generate_summary": generate_summary
            },
            queue=queue
        )
        return result.id
    
    def process_image(
        self,
        file_path: str,
        detect_objects: bool = True,
        face_recognition: bool = False,
        queue: str = "file_processing"
    ) -> str:
        """Process an image file"""
        result = process_image_task.apply_async(
            kwargs={
                "file_path": file_path,
                "detect_objects": detect_objects,
                "face_recognition": face_recognition
            },
            queue=queue
        )
        return result.id
    
    def process_audio(
        self,
        file_path: str,
        transcribe: bool = True,
        detect_speakers: bool = True,
        queue: str = "file_processing"
    ) -> str:
        """Process an audio file"""
        result = process_audio_task.apply_async(
            kwargs={
                "file_path": file_path,
                "transcribe": transcribe,
                "detect_speakers": detect_speakers
            },
            queue=queue
        )
        return result.id
    
    def process_video(
        self,
        file_path: str,
        extract_frames: bool = True,
        process_audio: bool = True,
        queue: str = "file_processing"
    ) -> str:
        """Process a video file"""
        result = process_video_task.apply_async(
            kwargs={
                "file_path": file_path,
                "extract_frames": extract_frames,
                "process_audio": process_audio
            },
            queue=queue
        )
        return result.id
    
    # AI Processing Methods
    def process_ai_request(
        self,
        request_type: str,
        input_data: Dict[str, Any],
        model_config: Optional[Dict[str, Any]] = None,
        queue: str = "ai_processing"
    ) -> str:
        """Process AI request"""
        result = process_ai_request_task.apply_async(
            kwargs={
                "request_type": request_type,
                "input_data": input_data,
                "model_config": model_config or {}
            },
            queue=queue
        )
        return result.id
    
    def generate_embedding(
        self,
        text: str,
        model: str = "text-embedding-ada-002",
        queue: str = "ai_processing"
    ) -> str:
        """Generate embedding for text"""
        result = generate_embedding_task.apply_async(
            kwargs={
                "text": text,
                "model": model
            },
            queue=queue
        )
        return result.id
    
    def process_chat(
        self,
        message: str,
        context: Optional[List[Dict[str, str]]] = None,
        model: str = "gpt-4",
        queue: str = "ai_processing"
    ) -> str:
        """Process chat message"""
        result = process_chat_task.apply_async(
            kwargs={
                "message": message,
                "context": context or [],
                "model": model
            },
            queue=queue
        )
        return result.id
    
    def analyze_sentiment(
        self,
        text: str,
        detailed: bool = True,
        queue: str = "ai_processing"
    ) -> str:
        """Analyze sentiment of text"""
        result = analyze_sentiment_task.apply_async(
            kwargs={
                "text": text,
                "detailed": detailed
            },
            queue=queue
        )
        return result.id
    
    # Cleanup Methods
    def cleanup_old_tasks(
        self,
        max_age_hours: int = 24,
        queue: str = "default"
    ) -> str:
        """Clean up old tasks"""
        result = cleanup_old_tasks.apply_async(
            kwargs={
                "max_age_hours": max_age_hours
            },
            queue=queue
        )
        return result.id
    
    def cleanup_old_files(
        self,
        directory: str,
        max_age_hours: int = 168,
        dry_run: bool = False,
        queue: str = "default"
    ) -> str:
        """Clean up old files"""
        result = cleanup_old_files.apply_async(
            kwargs={
                "directory": directory,
                "max_age_hours": max_age_hours,
                "dry_run": dry_run
            },
            queue=queue
        )
        return result.id
    
    def cleanup_old_logs(
        self,
        log_directory: str = "logs",
        max_age_hours: int = 168,
        queue: str = "default"
    ) -> str:
        """Clean up old logs"""
        result = cleanup_old_logs.apply_async(
            kwargs={
                "log_directory": log_directory,
                "max_age_hours": max_age_hours
            },
            queue=queue
        )
        return result.id
    
    # Monitoring Methods
    def health_check(self, queue: str = "default") -> str:
        """Perform health check"""
        result = health_check.apply_async(queue=queue)
        return result.id
    
    def get_system_stats(self, queue: str = "default") -> str:
        """Get system statistics"""
        result = system_stats.apply_async(queue=queue)
        return result.id
    
    def get_queue_stats(self, queue: str = "default") -> str:
        """Get queue statistics"""
        result = queue_stats.apply_async(queue=queue)
        return result.id
    
    # Scheduler Methods
    def process_pending_tasks(
        self,
        max_tasks: int = 100,
        task_types: Optional[List[str]] = None,
        queue: str = "default"
    ) -> str:
        """Process pending tasks"""
        result = process_pending_tasks.apply_async(
            kwargs={
                "max_tasks": max_tasks,
                "task_types": task_types
            },
            queue=queue
        )
        return result.id
    
    def retry_failed_tasks(
        self,
        max_retries: int = 3,
        max_age_hours: int = 24,
        task_types: Optional[List[str]] = None,
        queue: str = "default"
    ) -> str:
        """Retry failed tasks"""
        result = retry_failed_tasks.apply_async(
            kwargs={
                "max_retries": max_retries,
                "max_age_hours": max_age_hours,
                "task_types": task_types
            },
            queue=queue
        )
        return result.id
    
    # Twilio Call Methods
    def queue_outbound_call(
        self,
        to_number: str,
        from_number: Optional[str] = None,
        webhook_url: Optional[str] = None,
        status_callback_url: Optional[str] = None,
        call_metadata: Optional[Dict[str, Any]] = None,
        delay_seconds: int = 0,
        queue: str = "twilio_calls"
    ) -> str:
        """Queue an outbound call"""
        result = queue_outbound_call_task.apply_async(
            kwargs={
                "to_number": to_number,
                "from_number": from_number,
                "webhook_url": webhook_url,
                "status_callback_url": status_callback_url,
                "call_metadata": call_metadata,
                "delay_seconds": delay_seconds
            },
            queue=queue
        )
        return result.id
    
    def queue_bulk_calls(
        self,
        phone_numbers: List[str],
        from_number: Optional[str] = None,
        webhook_url: Optional[str] = None,
        status_callback_url: Optional[str] = None,
        call_metadata: Optional[Dict[str, Any]] = None,
        delay_between_calls: int = 5,
        max_concurrent_calls: int = 10,
        queue: str = "twilio_calls"
    ) -> str:
        """Queue multiple outbound calls"""
        result = queue_bulk_calls_task.apply_async(
            kwargs={
                "phone_numbers": phone_numbers,
                "from_number": from_number,
                "webhook_url": webhook_url,
                "status_callback_url": status_callback_url,
                "call_metadata": call_metadata,
                "delay_between_calls": delay_between_calls,
                "max_concurrent_calls": max_concurrent_calls
            },
            queue=queue
        )
        return result.id
    
    def schedule_call(
        self,
        to_number: str,
        from_number: Optional[str] = None,
        webhook_url: Optional[str] = None,
        status_callback_url: Optional[str] = None,
        call_metadata: Optional[Dict[str, Any]] = None,
        schedule_time: Optional[str] = None,
        timezone: str = "UTC",
        queue: str = "twilio_calls"
    ) -> str:
        """Schedule a call for a specific time"""
        result = schedule_call_task.apply_async(
            kwargs={
                "to_number": to_number,
                "from_number": from_number,
                "webhook_url": webhook_url,
                "status_callback_url": status_callback_url,
                "call_metadata": call_metadata,
                "schedule_time": schedule_time,
                "timezone": timezone
            },
            queue=queue
        )
        return result.id
    
    def retry_failed_call(
        self,
        original_task_id: str,
        to_number: str,
        from_number: Optional[str] = None,
        webhook_url: Optional[str] = None,
        status_callback_url: Optional[str] = None,
        call_metadata: Optional[Dict[str, Any]] = None,
        retry_delay: int = 300,
        queue: str = "twilio_calls"
    ) -> str:
        """Retry a failed call"""
        result = retry_failed_call_task.apply_async(
            kwargs={
                "original_task_id": original_task_id,
                "to_number": to_number,
                "from_number": from_number,
                "webhook_url": webhook_url,
                "status_callback_url": status_callback_url,
                "call_metadata": call_metadata,
                "retry_delay": retry_delay
            },
            queue=queue
        )
        return result.id
    
    def end_call(
        self,
        call_sid: str,
        queue: str = "twilio_calls"
    ) -> str:
        """End an active call"""
        result = end_call_task.apply_async(
            kwargs={"call_sid": call_sid},
            queue=queue
        )
        return result.id
    
    def cleanup_stale_calls(
        self,
        max_age_hours: int = 24,
        queue: str = "twilio_calls"
    ) -> str:
        """Clean up stale call records"""
        result = cleanup_stale_calls_task.apply_async(
            kwargs={"max_age_hours": max_age_hours},
            queue=queue
        )
        return result.id
    
    def get_call_status(
        self,
        call_sid: str,
        queue: str = "twilio_calls"
    ) -> str:
        """Get status of a specific call"""
        result = get_call_status_task.apply_async(
            kwargs={"call_sid": call_sid},
            queue=queue
        )
        return result.id
    
    # Task Management Methods
    def get_active_tasks(self) -> List[Dict[str, Any]]:
        """Get all active (PENDING, PROGRESS) tasks including those in queue"""
        try:
            inspect = celery_app.control.inspect()
            
            # Get active tasks from all workers
            active_tasks = inspect.active() or {}
            reserved_tasks = inspect.reserved() or {}
            
            active_task_list = []
            
            # Process active tasks (currently being executed)
            for worker_name, tasks in active_tasks.items():
                for task in tasks:
                    active_task_list.append({
                        "task_id": task.get("id"),
                        "name": task.get("name"),
                        "status": "PROGRESS",
                        "worker": worker_name,
                        "args": task.get("args", []),
                        "kwargs": task.get("kwargs", {}),
                        "time_start": task.get("time_start"),
                        "acknowledged": task.get("acknowledged", False)
                    })
            
            # Process reserved tasks (waiting to be executed)
            for worker_name, tasks in reserved_tasks.items():
                for task in tasks:
                    active_task_list.append({
                        "task_id": task.get("id"),
                        "name": task.get("name"),
                        "status": "PENDING",
                        "worker": worker_name,
                        "args": task.get("args", []),
                        "kwargs": task.get("kwargs", {}),
                        "time_start": None,
                        "acknowledged": task.get("acknowledged", False)
                    })
            
            # Get pending tasks from the queue (not yet picked up by workers)
            try:
                # Get queue statistics to see pending tasks
                queue_stats = inspect.stats() or {}
                pending_tasks = []
                
                # Check each worker's queue
                for worker_name, stats in queue_stats.items():
                    if 'pool' in stats and 'max-concurrency' in stats['pool']:
                        # Get tasks in the worker's queue
                        worker_reserved = reserved_tasks.get(worker_name, [])
                        worker_active = active_tasks.get(worker_name, [])
                        
                        # Calculate pending tasks (tasks in queue but not yet reserved)
                        max_concurrency = stats['pool']['max-concurrency']
                        current_tasks = len(worker_active) + len(worker_reserved)
                        
                        if current_tasks < max_concurrency:
                            # There might be pending tasks in the queue
                            pending_count = max_concurrency - current_tasks
                            if pending_count > 0:
                                pending_tasks.append({
                                    "worker": worker_name,
                                    "pending_count": pending_count,
                                    "max_concurrency": max_concurrency,
                                    "current_tasks": current_tasks
                                })
                
                # Add pending task information
                for pending_info in pending_tasks:
                    active_task_list.append({
                        "task_id": f"pending_{pending_info['worker']}_{len(active_task_list)}",
                        "name": "PENDING_TASKS",
                        "status": "PENDING",
                        "worker": pending_info['worker'],
                        "args": [],
                        "kwargs": {"pending_count": pending_info['pending_count']},
                        "time_start": None,
                        "acknowledged": False,
                        "pending_info": pending_info
                    })
                    
            except Exception as e:
                logger.warning(f"Could not get pending tasks from queue: {str(e)}")
            
            # Also check for tasks that are in the queue but not yet picked up by any worker
            try:
                # Get queue lengths from Redis
                from redis import Redis
                from config.settings import settings
                
                # Parse Redis URL to get connection details
                redis_url = settings.celery_broker_url
                if redis_url.startswith('redis://'):
                    redis_url = redis_url.replace('redis://', '')
                    if '@' in redis_url:
                        auth, rest = redis_url.split('@', 1)
                        password = auth.split(':')[1] if ':' in auth else None
                        host_port = rest.split('/')[0]
                    else:
                        password = None
                        host_port = redis_url.split('/')[0]
                    
                    host, port = host_port.split(':') if ':' in host_port else (host_port, 6379)
                    db = int(redis_url.split('/')[-1]) if '/' in redis_url else 0
                    
                    redis_client = Redis(host=host, port=int(port), password=password, db=db)
                    
                    # Check queue lengths for different queues
                    queues = ['celery', 'default', 'email', 'file_processing', 'ai_processing', 'twilio_calls']
                    for queue_name in queues:
                        queue_length = redis_client.llen(queue_name)
                        if queue_length > 0:
                            active_task_list.append({
                                "task_id": f"queue_{queue_name}_{len(active_task_list)}",
                                "name": f"QUEUE_{queue_name.upper()}",
                                "status": "PENDING",
                                "worker": "QUEUE",
                                "args": [],
                                "kwargs": {"queue_name": queue_name, "queue_length": queue_length},
                                "time_start": None,
                                "acknowledged": False,
                                "queue_info": {"queue_name": queue_name, "queue_length": queue_length}
                            })
                            logger.info(f"Found {queue_length} pending tasks in queue '{queue_name}'")
                    
            except Exception as e:
                logger.warning(f"Could not check Redis queues: {str(e)}")
            
            logger.info(f"Found {len(active_task_list)} active/pending tasks")
            return active_task_list
            
        except Exception as e:
            logger.error(f"Failed to get active tasks: {str(e)}")
            return []
    
    def kill_all_tasks(self) -> Dict[str, Any]:
        """Kill all running and pending tasks"""
        try:
            inspect = celery_app.control.inspect()
            
            # Get all active and reserved tasks
            active_tasks = inspect.active() or {}
            reserved_tasks = inspect.reserved() or {}
            
            killed_count = 0
            failed_count = 0
            failed_task_ids = []
            
            # Kill active tasks
            for worker_name, tasks in active_tasks.items():
                for task in tasks:
                    task_id = task.get("id")
                    if task_id:
                        try:
                            celery_app.control.revoke(task_id, terminate=True, signal="SIGKILL")
                            killed_count += 1
                            logger.info(f"Killed active task {task_id} on worker {worker_name}")
                        except Exception as e:
                            failed_count += 1
                            failed_task_ids.append(task_id)
                            logger.error(f"Failed to kill active task {task_id}: {str(e)}")
            
            # Kill reserved tasks
            for worker_name, tasks in reserved_tasks.items():
                for task in tasks:
                    task_id = task.get("id")
                    if task_id:
                        try:
                            celery_app.control.revoke(task_id, terminate=True)
                            killed_count += 1
                            logger.info(f"Killed reserved task {task_id} on worker {worker_name}")
                        except Exception as e:
                            failed_count += 1
                            failed_task_ids.append(task_id)
                            logger.error(f"Failed to kill reserved task {task_id}: {str(e)}")
            
            # Purge all queues to remove any remaining tasks
            try:
                celery_app.control.purge()
                logger.info("Purged all queues")
            except Exception as e:
                logger.error(f"Failed to purge queues: {str(e)}")
            
            result = {
                "killed_count": killed_count,
                "failed_count": failed_count,
                "failed_task_ids": failed_task_ids
            }
            
            logger.warning(f"Kill all tasks completed: {killed_count} killed, {failed_count} failed")
            return result
            
        except Exception as e:
            logger.error(f"Failed to kill all tasks: {str(e)}")
            return {
                "killed_count": 0,
                "failed_count": 1,
                "failed_task_ids": [],
                "error": str(e)
            }
    
    # Utility Methods
    def get_task_result(self, task_id: str, timeout: int = 30) -> Optional[Dict[str, Any]]:
        """Get task result"""
        try:
            result = celery_app.AsyncResult(task_id)
            return result.get(timeout=timeout)
        except Exception as e:
            logger.error(f"Failed to get task result for {task_id}: {str(e)}")
            return None
    
    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Get task status"""
        try:
            result = celery_app.AsyncResult(task_id)
            return {
                "task_id": task_id,
                "status": result.status,
                "info": result.info,
                "traceback": result.traceback
            }
        except Exception as e:
            logger.error(f"Failed to get task status for {task_id}: {str(e)}")
            return {
                "task_id": task_id,
                "status": "error",
                "error": str(e)
            }
    
    def cancel_task(self, task_id: str) -> bool:
        """Cancel a task"""
        try:
            celery_app.control.revoke(task_id, terminate=True)
            logger.info(f"Task {task_id} cancelled")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel task {task_id}: {str(e)}")
            return False
    
    def get_worker_stats(self) -> Dict[str, Any]:
        """Get worker statistics"""
        try:
            inspect = celery_app.control.inspect()
            return {
                "active": inspect.active(),
                "registered": inspect.registered(),
                "stats": inspect.stats(),
                "revoked": inspect.revoked()
            }
        except Exception as e:
            logger.error(f"Failed to get worker stats: {str(e)}")
            return {}
    
    def purge_queue(self, queue_name: str) -> bool:
        """Purge a queue"""
        try:
            celery_app.control.purge()
            logger.info(f"Queue {queue_name} purged")
            return True
        except Exception as e:
            logger.error(f"Failed to purge queue {queue_name}: {str(e)}")
            return False


# Global instance
celery_service = CeleryService()
