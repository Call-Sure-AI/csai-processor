"""
Scheduler Tasks for Celery
"""
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from celery import current_task
from loguru import logger
from .base import BaseTask
from services.celery_app import celery_app


@celery_app.task(base=BaseTask, bind=True)
def process_pending_tasks(
    self,
    max_tasks: int = 100,
    task_types: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Process pending tasks from database or queue
    
    Args:
        max_tasks: Maximum number of tasks to process
        task_types: List of task types to process
    
    Returns:
        Processing result
    """
    try:
        logger.info(f"Processing pending tasks (max: {max_tasks})")
        
        # Update task progress
        current_task.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": 100, "status": "Fetching pending tasks"}
        )
        
        # Simulate fetching pending tasks from database
        # In a real implementation, you would query your database
        pending_tasks = _get_pending_tasks_from_db(max_tasks, task_types)
        
        current_task.update_state(
            state="PROGRESS",
            meta={"current": 30, "total": 100, "status": f"Found {len(pending_tasks)} pending tasks"}
        )
        
        processed_count = 0
        failed_count = 0
        results = []
        
        for i, task_data in enumerate(pending_tasks):
            try:
                # Update progress
                progress = 30 + (i / len(pending_tasks)) * 60
                current_task.update_state(
                    state="PROGRESS",
                    meta={
                        "current": int(progress),
                        "total": 100,
                        "status": f"Processing task {i+1}/{len(pending_tasks)}"
                    }
                )
                
                # Process task based on type
                result = _process_scheduled_task(task_data)
                results.append(result)
                processed_count += 1
                
                # Small delay to prevent overwhelming the system
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Failed to process pending task {task_data.get('id')}: {str(e)}")
                failed_count += 1
                results.append({
                    "task_id": task_data.get('id'),
                    "status": "failed",
                    "error": str(e)
                })
        
        current_task.update_state(
            state="PROGRESS",
            meta={"current": 100, "total": 100, "status": "Processing completed"}
        )
        
        result = {
            "total_pending": len(pending_tasks),
            "processed": processed_count,
            "failed": failed_count,
            "results": results,
            "processed_at": datetime.utcnow().isoformat()
        }
        
        logger.info(f"Pending tasks processed: {processed_count} successful, {failed_count} failed")
        return result
        
    except Exception as e:
        logger.error(f"Pending tasks processing failed: {str(e)}")
        raise


def _get_pending_tasks_from_db(max_tasks: int, task_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Get pending tasks from database (simulated)"""
    # This is a simulation - replace with actual database query
    import random
    
    task_types = task_types or ["email", "file_processing", "ai_processing"]
    
    pending_tasks = []
    for i in range(min(max_tasks, random.randint(5, 20))):
        task_data = {
            "id": f"pending_task_{i}",
            "type": random.choice(task_types),
            "priority": random.choice(["high", "normal", "low"]),
            "scheduled_at": datetime.utcnow().isoformat(),
            "data": {
                "message": f"Test message {i}",
                "recipient": f"user{i}@example.com"
            }
        }
        pending_tasks.append(task_data)
    
    return pending_tasks


def _process_scheduled_task(task_data: Dict[str, Any]) -> Dict[str, Any]:
    """Process a scheduled task"""
    task_type = task_data.get("type")
    task_id = task_data.get("id")
    
    try:
        if task_type == "email":
            # Queue email task
            result = celery_app.send_task(
                "services.celery_tasks.email.send_email_task",
                kwargs=task_data.get("data", {}),
                queue="email"
            )
            return {
                "task_id": task_id,
                "celery_task_id": result.id,
                "status": "queued",
                "queue": "email"
            }
        
        elif task_type == "file_processing":
            # Queue file processing task
            result = celery_app.send_task(
                "services.celery_tasks.file_processing.process_file_task",
                kwargs=task_data.get("data", {}),
                queue="file_processing"
            )
            return {
                "task_id": task_id,
                "celery_task_id": result.id,
                "status": "queued",
                "queue": "file_processing"
            }
        
        elif task_type == "ai_processing":
            # Queue AI processing task
            result = celery_app.send_task(
                "services.celery_tasks.ai_processing.process_ai_request_task",
                kwargs=task_data.get("data", {}),
                queue="ai_processing"
            )
            return {
                "task_id": task_id,
                "celery_task_id": result.id,
                "status": "queued",
                "queue": "ai_processing"
            }
        
        else:
            raise ValueError(f"Unknown task type: {task_type}")
    
    except Exception as e:
        logger.error(f"Failed to queue task {task_id}: {str(e)}")
        raise


@celery_app.task(base=BaseTask, bind=True)
def retry_failed_tasks(
    self,
    max_retries: int = 3,
    max_age_hours: int = 24,
    task_types: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Retry failed tasks
    
    Args:
        max_retries: Maximum number of retries per task
        max_age_hours: Maximum age of failed tasks to retry
        task_types: List of task types to retry
    
    Returns:
        Retry result
    """
    try:
        logger.info(f"Retrying failed tasks (max retries: {max_retries}, max age: {max_age_hours}h)")
        
        # Update task progress
        current_task.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": 100, "status": "Fetching failed tasks"}
        )
        
        # Get failed tasks from database
        failed_tasks = _get_failed_tasks_from_db(max_retries, max_age_hours, task_types)
        
        current_task.update_state(
            state="PROGRESS",
            meta={"current": 30, "total": 100, "status": f"Found {len(failed_tasks)} failed tasks"}
        )
        
        retried_count = 0
        failed_count = 0
        results = []
        
        for i, task_data in enumerate(failed_tasks):
            try:
                # Update progress
                progress = 30 + (i / len(failed_tasks)) * 60
                current_task.update_state(
                    state="PROGRESS",
                    meta={
                        "current": int(progress),
                        "total": 100,
                        "status": f"Retrying task {i+1}/{len(failed_tasks)}"
                    }
                )
                
                # Retry task
                result = _retry_failed_task(task_data)
                results.append(result)
                retried_count += 1
                
                # Small delay
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Failed to retry task {task_data.get('id')}: {str(e)}")
                failed_count += 1
                results.append({
                    "task_id": task_data.get('id'),
                    "status": "retry_failed",
                    "error": str(e)
                })
        
        current_task.update_state(
            state="PROGRESS",
            meta={"current": 100, "total": 100, "status": "Retry processing completed"}
        )
        
        result = {
            "total_failed": len(failed_tasks),
            "retried": retried_count,
            "failed": failed_count,
            "results": results,
            "processed_at": datetime.utcnow().isoformat()
        }
        
        logger.info(f"Failed tasks retried: {retried_count} successful, {failed_count} failed")
        return result
        
    except Exception as e:
        logger.error(f"Failed tasks retry processing failed: {str(e)}")
        raise


def _get_failed_tasks_from_db(max_retries: int, max_age_hours: int, task_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Get failed tasks from database (simulated)"""
    # This is a simulation - replace with actual database query
    import random
    
    task_types = task_types or ["email", "file_processing", "ai_processing"]
    
    failed_tasks = []
    for i in range(random.randint(3, 10)):
        task_data = {
            "id": f"failed_task_{i}",
            "type": random.choice(task_types),
            "retry_count": random.randint(0, max_retries - 1),
            "failed_at": (datetime.utcnow() - timedelta(hours=random.randint(1, max_age_hours))).isoformat(),
            "error": "Simulated failure",
            "data": {
                "message": f"Retry message {i}",
                "recipient": f"user{i}@example.com"
            }
        }
        failed_tasks.append(task_data)
    
    return failed_tasks


def _retry_failed_task(task_data: Dict[str, Any]) -> Dict[str, Any]:
    """Retry a failed task"""
    task_type = task_data.get("type")
    task_id = task_data.get("id")
    retry_count = task_data.get("retry_count", 0)
    
    try:
        # Update retry count
        task_data["retry_count"] = retry_count + 1
        
        # Queue task for retry
        if task_type == "email":
            result = celery_app.send_task(
                "services.celery_tasks.email.send_email_task",
                kwargs=task_data.get("data", {}),
                queue="email"
            )
        elif task_type == "file_processing":
            result = celery_app.send_task(
                "services.celery_tasks.file_processing.process_file_task",
                kwargs=task_data.get("data", {}),
                queue="file_processing"
            )
        elif task_type == "ai_processing":
            result = celery_app.send_task(
                "services.celery_tasks.ai_processing.process_ai_request_task",
                kwargs=task_data.get("data", {}),
                queue="ai_processing"
            )
        else:
            raise ValueError(f"Unknown task type: {task_type}")
        
        return {
            "task_id": task_id,
            "celery_task_id": result.id,
            "status": "retried",
            "retry_count": task_data["retry_count"],
            "queue": task_type
        }
    
    except Exception as e:
        logger.error(f"Failed to retry task {task_id}: {str(e)}")
        raise
