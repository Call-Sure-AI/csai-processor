"""
Background Worker Implementation
"""
import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
import traceback

from core.interfaces import IBackgroundWorker, ILogger
from config.settings import settings


class TaskStatus(Enum):
    """Task status enumeration"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """Task data class"""
    id: str
    func: Callable
    args: tuple
    kwargs: dict
    status: TaskStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Any = None
    error: Optional[str] = None
    retries: int = 0
    max_retries: int = 3


class BackgroundWorker(IBackgroundWorker):
    """
    Background task processor with proper error handling and retry logic
    """
    
    def __init__(
        self,
        max_workers: int = None,
        logger: Optional[ILogger] = None
    ):
        self.max_workers = max_workers or settings.background_worker_concurrency
        self.logger = logger or logging.getLogger(__name__)
        
        # Task management
        self.task_queue = asyncio.Queue()
        self.tasks: Dict[str, Task] = {}
        self.workers: List[asyncio.Task] = []
        
        # Worker state
        self.running = False
        self.stopped = False
        
        # Statistics
        self.stats = {
            "tasks_processed": 0,
            "tasks_failed": 0,
            "tasks_retried": 0,
            "total_processing_time": 0.0
        }

    def enqueue(self, func: Callable, *args, **kwargs) -> str:
        """Enqueue a background task"""
        import uuid
        
        task_id = str(uuid.uuid4())
        task = Task(
            id=task_id,
            func=func,
            args=args,
            kwargs=kwargs,
            status=TaskStatus.PENDING,
            created_at=datetime.utcnow()
        )
        
        self.tasks[task_id] = task
        asyncio.create_task(self.task_queue.put(task))
        
        self.logger.info(f"Enqueued task {task_id}: {func.__name__}")
        return task_id

    async def start(self) -> None:
        """Start the background worker"""
        if self.running:
            self.logger.warning("Background worker is already running")
            return
        
        self.running = True
        self.stopped = False
        
        # Start worker tasks
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(f"worker-{i}"))
            self.workers.append(worker)
        
        self.logger.info(f"Started background worker with {self.max_workers} workers")

    async def stop(self) -> None:
        """Stop the background worker gracefully"""
        if not self.running:
            return
        
        self.logger.info("Stopping background worker...")
        self.running = False
        self.stopped = True
        
        # Wait for all workers to complete
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)
        
        # Wait for remaining tasks to complete
        while not self.task_queue.empty():
            try:
                task = await asyncio.wait_for(self.task_queue.get(), timeout=1.0)
                await self._process_task(task)
            except asyncio.TimeoutError:
                break
        
        self.logger.info("Background worker stopped")

    async def _worker(self, worker_name: str) -> None:
        """Worker task that processes queued tasks"""
        self.logger.info(f"Worker {worker_name} started")
        
        while self.running:
            try:
                # Get task from queue with timeout
                task = await asyncio.wait_for(
                    self.task_queue.get(),
                    timeout=1.0
                )
                
                await self._process_task(task)
                
            except asyncio.TimeoutError:
                # No tasks available, continue loop
                continue
            except Exception as e:
                self.logger.error(f"Worker {worker_name} error: {str(e)}")
                await asyncio.sleep(1)  # Brief pause before continuing
        
        self.logger.info(f"Worker {worker_name} stopped")

    async def _process_task(self, task: Task) -> None:
        """Process a single task"""
        start_time = datetime.utcnow()
        
        try:
            # Update task status
            task.status = TaskStatus.RUNNING
            task.started_at = start_time
            
            self.logger.info(f"Processing task {task.id}: {task.func.__name__}")
            
            # Execute task
            if asyncio.iscoroutinefunction(task.func):
                result = await task.func(*task.args, **task.kwargs)
            else:
                # Run sync function in thread pool
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, task.func, *task.args, **task.kwargs
                )
            
            # Update task with result
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.utcnow()
            task.result = result
            
            # Update statistics
            processing_time = (task.completed_at - task.started_at).total_seconds()
            self.stats["tasks_processed"] += 1
            self.stats["total_processing_time"] += processing_time
            
            self.logger.info(f"Task {task.id} completed in {processing_time:.3f}s")
            
        except Exception as e:
            # Handle task failure
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.utcnow()
            task.error = str(e)
            
            # Update statistics
            self.stats["tasks_failed"] += 1
            
            # Retry logic
            if task.retries < task.max_retries:
                task.retries += 1
                task.status = TaskStatus.PENDING
                task.started_at = None
                task.completed_at = None
                task.error = None
                
                # Re-queue task with exponential backoff
                delay = min(2 ** task.retries, 60)  # Max 60 seconds
                asyncio.create_task(self._retry_task(task, delay))
                
                self.stats["tasks_retried"] += 1
                self.logger.warning(f"Task {task.id} failed, retrying in {delay}s (attempt {task.retries}/{task.max_retries})")
            else:
                self.logger.error(f"Task {task.id} failed permanently after {task.max_retries} retries: {str(e)}")
                self.logger.error(f"Task traceback: {traceback.format_exc()}")

    async def _retry_task(self, task: Task, delay: int) -> None:
        """Retry a failed task after delay"""
        await asyncio.sleep(delay)
        if self.running:
            await self.task_queue.put(task)

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a specific task"""
        task = self.tasks.get(task_id)
        if not task:
            return None
        
        return {
            "id": task.id,
            "status": task.status.value,
            "created_at": task.created_at.isoformat(),
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "retries": task.retries,
            "max_retries": task.max_retries,
            "error": task.error,
            "result": task.result
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get worker statistics"""
        return {
            **self.stats,
            "active_workers": len(self.workers),
            "queue_size": self.task_queue.qsize(),
            "total_tasks": len(self.tasks),
            "running": self.running,
            "stopped": self.stopped
        }

    def cleanup_old_tasks(self, max_age_hours: int = 24) -> int:
        """Clean up old completed tasks"""
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        old_tasks = [
            task_id for task_id, task in self.tasks.items()
            if task.completed_at and task.completed_at < cutoff
        ]
        
        for task_id in old_tasks:
            self.tasks.pop(task_id, None)
        
        self.logger.info(f"Cleaned up {len(old_tasks)} old tasks")
        return len(old_tasks)

    async def wait_for_task(self, task_id: str, timeout: float = 60.0) -> Optional[Any]:
        """Wait for a specific task to complete"""
        start_time = datetime.utcnow()
        
        while (datetime.utcnow() - start_time).total_seconds() < timeout:
            task = self.tasks.get(task_id)
            if not task:
                return None
            
            if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
                return task.result if task.status == TaskStatus.COMPLETED else None
            
            await asyncio.sleep(0.1)
        
        raise asyncio.TimeoutError(f"Task {task_id} did not complete within {timeout}s")

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending task"""
        task = self.tasks.get(task_id)
        if not task or task.status != TaskStatus.PENDING:
            return False
        
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.utcnow()
        task.error = "Task cancelled"
        
        self.logger.info(f"Task {task_id} cancelled")
        return True
