"""
Monitoring Tasks for Celery
"""
import time
import psutil
import os
from datetime import datetime
from typing import Dict, Any, List
from celery import current_task
from loguru import logger
from .base import BaseTask
from services.celery_app import celery_app


@celery_app.task(base=BaseTask, bind=True)
def health_check(self) -> Dict[str, Any]:
    """
    Perform system health check
    
    Returns:
        Health check result
    """
    try:
        logger.info("Starting system health check")
        
        # Update task progress
        current_task.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": 100, "status": "Checking system resources"}
        )
        
        # Check system resources
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        current_task.update_state(
            state="PROGRESS",
            meta={"current": 30, "total": 100, "status": "Checking Celery workers"}
        )
        
        # Check Celery workers
        inspect = celery_app.control.inspect()
        active_workers = inspect.active()
        registered_workers = inspect.registered()
        stats = inspect.stats()
        
        current_task.update_state(
            state="PROGRESS",
            meta={"current": 60, "total": 100, "status": "Checking queues"}
        )
        
        # Check queue status
        queue_stats = {}
        for queue_name in ['default', 'high_priority', 'low_priority', 'email', 'file_processing', 'ai_processing']:
            try:
                # This is a simplified queue check
                queue_stats[queue_name] = {
                    "status": "active",
                    "message_count": 0  # Would need Redis client to get actual count
                }
            except Exception as e:
                queue_stats[queue_name] = {
                    "status": "error",
                    "error": str(e)
                }
        
        current_task.update_state(
            state="PROGRESS",
            meta={"current": 90, "total": 100, "status": "Finalizing health check"}
        )
        
        # Determine overall health status
        health_status = "healthy"
        warnings = []
        
        if cpu_percent > 80:
            health_status = "warning"
            warnings.append(f"High CPU usage: {cpu_percent}%")
        
        if memory.percent > 85:
            health_status = "warning"
            warnings.append(f"High memory usage: {memory.percent}%")
        
        if disk.percent > 90:
            health_status = "critical"
            warnings.append(f"Low disk space: {100 - disk.percent}% free")
        
        if not active_workers:
            health_status = "critical"
            warnings.append("No active Celery workers")
        
        result = {
            "status": health_status,
            "timestamp": datetime.utcnow().isoformat(),
            "system": {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_available_gb": round(memory.available / (1024**3), 2),
                "disk_percent": disk.percent,
                "disk_free_gb": round(disk.free / (1024**3), 2)
            },
            "celery": {
                "active_workers": len(active_workers) if active_workers else 0,
                "registered_workers": len(registered_workers) if registered_workers else 0,
                "worker_stats": stats or {}
            },
            "queues": queue_stats,
            "warnings": warnings
        }
        
        current_task.update_state(
            state="PROGRESS",
            meta={"current": 100, "total": 100, "status": "Health check completed"}
        )
        
        logger.info(f"Health check completed: {health_status}")
        return result
        
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        raise


@celery_app.task(base=BaseTask, bind=True)
def system_stats(self) -> Dict[str, Any]:
    """
    Collect system statistics
    
    Returns:
        System statistics
    """
    try:
        logger.info("Collecting system statistics")
        
        # Get system information
        cpu_count = psutil.cpu_count()
        cpu_freq = psutil.cpu_freq()
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage('/')
        
        # Get network statistics
        network = psutil.net_io_counters()
        
        # Get process information
        process = psutil.Process()
        process_memory = process.memory_info()
        process_cpu = process.cpu_percent()
        
        # Get Celery statistics
        inspect = celery_app.control.inspect()
        active_tasks = inspect.active()
        reserved_tasks = inspect.reserved()
        revoked_tasks = inspect.revoked()
        
        stats = {
            "timestamp": datetime.utcnow().isoformat(),
            "system": {
                "cpu": {
                    "count": cpu_count,
                    "frequency_mhz": cpu_freq.current if cpu_freq else None,
                    "percent": psutil.cpu_percent(interval=1)
                },
                "memory": {
                    "total_gb": round(memory.total / (1024**3), 2),
                    "available_gb": round(memory.available / (1024**3), 2),
                    "used_gb": round(memory.used / (1024**3), 2),
                    "percent": memory.percent
                },
                "swap": {
                    "total_gb": round(swap.total / (1024**3), 2),
                    "used_gb": round(swap.used / (1024**3), 2),
                    "percent": swap.percent
                },
                "disk": {
                    "total_gb": round(disk.total / (1024**3), 2),
                    "used_gb": round(disk.used / (1024**3), 2),
                    "free_gb": round(disk.free / (1024**3), 2),
                    "percent": disk.percent
                },
                "network": {
                    "bytes_sent": network.bytes_sent,
                    "bytes_recv": network.bytes_recv,
                    "packets_sent": network.packets_sent,
                    "packets_recv": network.packets_recv
                }
            },
            "process": {
                "pid": process.pid,
                "memory_rss_mb": round(process_memory.rss / (1024**2), 2),
                "memory_vms_mb": round(process_memory.vms / (1024**2), 2),
                "cpu_percent": process_cpu,
                "num_threads": process.num_threads(),
                "create_time": datetime.fromtimestamp(process.create_time()).isoformat()
            },
            "celery": {
                "active_tasks": len(active_tasks) if active_tasks else 0,
                "reserved_tasks": len(reserved_tasks) if reserved_tasks else 0,
                "revoked_tasks": len(revoked_tasks) if revoked_tasks else 0,
                "task_details": {
                    "active": active_tasks or {},
                    "reserved": reserved_tasks or {},
                    "revoked": revoked_tasks or {}
                }
            }
        }
        
        logger.info("System statistics collected successfully")
        return stats
        
    except Exception as e:
        logger.error(f"System stats collection failed: {str(e)}")
        raise


@celery_app.task(base=BaseTask, bind=True)
def queue_stats(self) -> Dict[str, Any]:
    """
    Collect queue statistics
    
    Returns:
        Queue statistics
    """
    try:
        logger.info("Collecting queue statistics")
        
        # Get queue information
        inspect = celery_app.control.inspect()
        active_tasks = inspect.active()
        reserved_tasks = inspect.reserved()
        revoked_tasks = inspect.revoked()
        
        # Calculate queue statistics
        queue_stats = {}
        total_active = 0
        total_reserved = 0
        total_revoked = 0
        
        # Process active tasks
        if active_tasks:
            for worker, tasks in active_tasks.items():
                for task in tasks:
                    queue_name = task.get('delivery_info', {}).get('routing_key', 'default')
                    if queue_name not in queue_stats:
                        queue_stats[queue_name] = {
                            "active": 0,
                            "reserved": 0,
                            "revoked": 0,
                            "workers": set()
                        }
                    queue_stats[queue_name]["active"] += 1
                    queue_stats[queue_name]["workers"].add(worker)
                    total_active += 1
        
        # Process reserved tasks
        if reserved_tasks:
            for worker, tasks in reserved_tasks.items():
                for task in tasks:
                    queue_name = task.get('delivery_info', {}).get('routing_key', 'default')
                    if queue_name not in queue_stats:
                        queue_stats[queue_name] = {
                            "active": 0,
                            "reserved": 0,
                            "revoked": 0,
                            "workers": set()
                        }
                    queue_stats[queue_name]["reserved"] += 1
                    queue_stats[queue_name]["workers"].add(worker)
                    total_reserved += 1
        
        # Process revoked tasks
        if revoked_tasks:
            for worker, tasks in revoked_tasks.items():
                for task in tasks:
                    queue_name = task.get('delivery_info', {}).get('routing_key', 'default')
                    if queue_name not in queue_stats:
                        queue_stats[queue_name] = {
                            "active": 0,
                            "reserved": 0,
                            "revoked": 0,
                            "workers": set()
                        }
                    queue_stats[queue_name]["revoked"] += 1
                    queue_stats[queue_name]["workers"].add(worker)
                    total_revoked += 1
        
        # Convert sets to lists for JSON serialization
        for queue_name in queue_stats:
            queue_stats[queue_name]["workers"] = list(queue_stats[queue_name]["workers"])
        
        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "total_tasks": {
                "active": total_active,
                "reserved": total_reserved,
                "revoked": total_revoked
            },
            "queues": queue_stats,
            "worker_count": len(set().union(*[stats["workers"] for stats in queue_stats.values()]))
        }
        
        logger.info(f"Queue statistics collected: {total_active} active, {total_reserved} reserved, {total_revoked} revoked")
        return result
        
    except Exception as e:
        logger.error(f"Queue stats collection failed: {str(e)}")
        raise
