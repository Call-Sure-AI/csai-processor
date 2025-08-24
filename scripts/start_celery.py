#!/usr/bin/env python3
"""
Celery Startup Script
"""
import os
import sys
import argparse
import subprocess
from pathlib import Path

# Add the src directory to Python path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

# Import settings after setting up the path
try:
    from config.settings import settings
except ImportError:
    print(f"Error: Could not import config.settings. Make sure you're running from the project root.")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Python path: {sys.path}")
    sys.exit(1)


def start_worker(queues=None, concurrency=None, loglevel="INFO"):
    """Start Celery worker"""
    from services.celery_app import celery_app
    
    queues = queues or ["default", "email", "file_processing", "ai_processing", "twilio_calls"]
    concurrency = concurrency or settings.celery_worker_concurrency
    
    # Set up environment with correct Python path
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_path) + os.pathsep + env.get("PYTHONPATH", "")
    
    cmd = [
        "celery", "-A", "services.celery_app:celery_app", "worker",
        "--loglevel", loglevel,
        "--concurrency", "1",  
        "--pool", "solo",  
        "--queues", ",".join(queues),
        "--hostname", f"worker@%h",
        "--max-tasks-per-child", str(settings.celery_worker_max_tasks_per_child),
        "--prefetch-multiplier", str(settings.celery_worker_prefetch_multiplier)
    ]
    
    print(f"Starting Celery worker with queues: {queues}")
    print(f"Command: {' '.join(cmd)}")
    print(f"Python path: {env['PYTHONPATH']}")
    
    try:
        subprocess.run(cmd, check=True, env=env)
    except KeyboardInterrupt:
        print("\nWorker stopped by user")
    except subprocess.CalledProcessError as e:
        print(f"Worker failed to start: {e}")
        sys.exit(1)


def start_beat():
    """Start Celery beat scheduler"""
    from services.celery_app import celery_app
    
    # Set up environment with correct Python path
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_path) + os.pathsep + env.get("PYTHONPATH", "")
    
    cmd = [
        "celery", "-A", "services.celery_app:celery_app", "beat",
        "--loglevel", "INFO",
        "--scheduler", "celery.beat.PersistentScheduler"
    ]
    
    print("Starting Celery beat scheduler")
    print(f"Command: {' '.join(cmd)}")
    print(f"Python path: {env['PYTHONPATH']}")
    
    try:
        subprocess.run(cmd, check=True, env=env)
    except KeyboardInterrupt:
        print("\nBeat scheduler stopped by user")
    except subprocess.CalledProcessError as e:
        print(f"Beat scheduler failed to start: {e}")
        sys.exit(1)


def start_flower():
    """Start Flower monitoring"""
    from services.celery_app import celery_app
    
    # Set up environment with correct Python path
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_path) + os.pathsep + env.get("PYTHONPATH", "")
    
    # Fixed Flower command - use equals sign for port
    cmd = [
        "celery", "-A", "services.celery_app:celery_app", "flower",
        "--port=5555",
        "--broker=" + settings.celery_broker_url,
        "--result-backend=" + settings.celery_result_backend
    ]
    
    print("Starting Flower monitoring")
    print(f"Command: {' '.join(cmd)}")
    print(f"Python path: {env['PYTHONPATH']}")
    print("Flower will be available at: http://localhost:5555")
    
    try:
        subprocess.run(cmd, check=True, env=env)
    except KeyboardInterrupt:
        print("\nFlower stopped by user")
    except subprocess.CalledProcessError as e:
        print(f"Flower failed to start: {e}")
        sys.exit(1)


def start_all():
    """Start all Celery components"""
    print("Starting all Celery components...")
    
    # Set up environment with correct Python path
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_path) + os.pathsep + env.get("PYTHONPATH", "")
    
    # Start worker in background
    worker_cmd = [
        "celery", "-A", "services.celery_app:celery_app", "worker",
        "--loglevel", "INFO",
        "--concurrency", str(settings.celery_worker_concurrency),
        "--queues", "default,email,file_processing,ai_processing,high_priority,low_priority",
        "--hostname", "worker@%h"
    ]
    
    # Start beat in background
    beat_cmd = [
        "celery", "-A", "services.celery_app:celery_app", "beat",
        "--loglevel", "INFO"
    ]
    
    # Start flower in background
    flower_cmd = [
        "celery", "-A", "services.celery_app:celery_app", "flower",
        "--port", "5555"
    ]
    
    try:
        # Start worker
        worker_process = subprocess.Popen(worker_cmd, env=env)
        print(f"Worker started with PID: {worker_process.pid}")
        
        # Start beat
        beat_process = subprocess.Popen(beat_cmd, env=env)
        print(f"Beat scheduler started with PID: {beat_process.pid}")
        
        # Start flower
        flower_process = subprocess.Popen(flower_cmd, env=env)
        print(f"Flower started with PID: {flower_process.pid}")
        
        print("\nAll Celery components started!")
        print("Flower monitoring: http://localhost:5555")
        print("Press Ctrl+C to stop all components")
        
        # Wait for processes
        try:
            worker_process.wait()
            beat_process.wait()
            flower_process.wait()
        except KeyboardInterrupt:
            print("\nStopping all components...")
            worker_process.terminate()
            beat_process.terminate()
            flower_process.terminate()
            
            worker_process.wait()
            beat_process.wait()
            flower_process.wait()
            
            print("All components stopped")
    
    except Exception as e:
        print(f"Failed to start components: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Celery Service Management")
    parser.add_argument(
        "command",
        choices=["worker", "beat", "flower", "all"],
        help="Command to run"
    )
    parser.add_argument(
        "--queues",
        nargs="+",
        default=["default"],
        help="Queues for worker (default: default)"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=settings.celery_worker_concurrency,
        help=f"Number of worker processes (default: {settings.celery_worker_concurrency})"
    )
    parser.add_argument(
        "--loglevel",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level (default: INFO)"
    )
    
    args = parser.parse_args()
    
    # Set environment
    os.environ.setdefault("CELERY_CONFIG_MODULE", "services.celery_app")
    
    if args.command == "worker":
        start_worker(args.queues, args.concurrency, args.loglevel)
    elif args.command == "beat":
        start_beat()
    elif args.command == "flower":
        start_flower()
    elif args.command == "all":
        start_all()


if __name__ == "__main__":
    main()
