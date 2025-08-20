"""
Base Task Class for Celery Tasks
"""
import time
import traceback
from typing import Any, Dict, Optional
from celery import Task
from loguru import logger
from config.settings import settings


class BaseTask(Task):
    """
    Base task class with common functionality for all Celery tasks
    """
    
    # Default task configuration
    abstract = True
    autoretry_for = (Exception,)
    max_retries = 3
    retry_backoff = True
    retry_backoff_max = 600  # 10 minutes
    retry_jitter = True
    
    def __init__(self):
        self.start_time = None
        self.task_id = None
    
    def on_success(self, retval, task_id, args, kwargs):
        """Called when task succeeds"""
        execution_time = time.time() - self.start_time if self.start_time else 0
        logger.info(
            f"Task {task_id} completed successfully in {execution_time:.2f}s",
            extra={
                "task_id": task_id,
                "task_name": self.name,
                "execution_time": execution_time,
                "status": "success"
            }
        )
    
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Called when task fails"""
        execution_time = time.time() - self.start_time if self.start_time else 0
        logger.error(
            f"Task {task_id} failed after {execution_time:.2f}s: {str(exc)}",
            extra={
                "task_id": task_id,
                "task_name": self.name,
                "execution_time": execution_time,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "status": "failure"
            }
        )
    
    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Called when task is retried"""
        logger.warning(
            f"Task {task_id} being retried: {str(exc)}",
            extra={
                "task_id": task_id,
                "task_name": self.name,
                "error": str(exc),
                "retry_count": self.request.retries,
                "status": "retry"
            }
        )
    
    def __call__(self, *args, **kwargs):
        """Called when task is executed"""
        self.start_time = time.time()
        self.task_id = self.request.id
        
        logger.info(
            f"Starting task {self.task_id}: {self.name}",
            extra={
                "task_id": self.task_id,
                "task_name": self.name,
                "args": args,
                "kwargs": kwargs,
                "status": "started"
            }
        )
        
        try:
            result = super().__call__(*args, **kwargs)
            return result
        except Exception as exc:
            logger.error(
                f"Task {self.task_id} raised exception: {str(exc)}",
                extra={
                    "task_id": self.task_id,
                    "task_name": self.name,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "status": "exception"
                }
            )
            raise
    
    def get_task_info(self) -> Dict[str, Any]:
        """Get task information"""
        return {
            "task_id": self.task_id,
            "task_name": self.name,
            "start_time": self.start_time,
            "execution_time": time.time() - self.start_time if self.start_time else 0,
            "retries": getattr(self.request, 'retries', 0),
            "max_retries": self.max_retries,
        }
    
    @classmethod
    def get_task_config(cls) -> Dict[str, Any]:
        """Get task configuration"""
        return {
            "name": cls.name,
            "max_retries": cls.max_retries,
            "autoretry_for": cls.autoretry_for,
            "retry_backoff": cls.retry_backoff,
            "retry_backoff_max": cls.retry_backoff_max,
            "retry_jitter": cls.retry_jitter,
        }


def create_task_with_retry(
    task_class: type,
    max_retries: int = 3,
    autoretry_for: tuple = (Exception,),
    retry_backoff: bool = True,
    retry_backoff_max: int = 600,
    retry_jitter: bool = True,
    **kwargs
) -> type:
    """
    Create a task class with custom retry configuration
    
    Args:
        task_class: The base task class
        max_retries: Maximum number of retries
        autoretry_for: Exceptions to auto-retry
        retry_backoff: Enable exponential backoff
        retry_backoff_max: Maximum retry delay
        retry_jitter: Add jitter to retry delays
        **kwargs: Additional task configuration
    
    Returns:
        Configured task class
    """
    class ConfiguredTask(task_class):
        abstract = True
        max_retries = max_retries
        autoretry_for = autoretry_for
        retry_backoff = retry_backoff
        retry_backoff_max = retry_backoff_max
        retry_jitter = retry_jitter
        
        for key, value in kwargs.items():
            setattr(ConfiguredTask, key, value)
    
    return ConfiguredTask
