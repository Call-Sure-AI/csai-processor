#!/usr/bin/env python3
"""
Simple Celery Worker Startup Script
"""
import os
import sys
from pathlib import Path

# Add src to Python path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

def start_worker():
    """Start Celery worker"""
    try:
        from services.celery_app import celery_app
        
        print("Starting Celery worker...")
        print(f"Python path: {sys.path[0]}")
        print(f"Broker: {celery_app.conf.broker_url}")
        print(f"Queues: twilio_calls")
        
        # Start the worker
        celery_app.worker_main([
            'worker',
            '--loglevel=INFO',
            '--queues=twilio_calls',
            '--concurrency=1',
            '--pool=solo',
            '--hostname=worker@%h'
        ])
        
    except ImportError as e:
        print(f"Import error: {e}")
        print("Make sure you're in the project root directory")
    except Exception as e:
        print(f"Failed to start worker: {e}")

if __name__ == "__main__":
    start_worker()
