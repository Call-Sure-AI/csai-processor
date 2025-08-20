#!/usr/bin/env python3
"""
Example: Using Celery to Queue Twilio Calls

This example demonstrates how to use the Celery service to queue various types of Twilio calls.
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent
sys.path.insert(0, str(src_path))

from services.celery_service import celery_service
from config.settings import settings

def example_single_call():
    """Example: Queue a single outbound call"""
    print("=== Single Call Example ===")
    
    # Queue a single call
    task_id = celery_service.queue_outbound_call(
        to_number="+1234567890",  # Replace with actual number
        from_number=settings.twilio_phone_number,
        call_metadata={
            "customer_id": "12345",
            "call_type": "follow_up",
            "agent_id": "agent_001"
        },
        delay_seconds=10  # Delay for 10 seconds
    )
    
    print(f"Queued single call with task ID: {task_id}")
    
    # Monitor the task
    monitor_task(task_id, "Single Call")
    
    return task_id

def example_bulk_calls():
    """Example: Queue multiple calls with rate limiting"""
    print("\n=== Bulk Calls Example ===")
    
    # List of phone numbers to call
    phone_numbers = [
        "+1234567890",  # Replace with actual numbers
        "+1234567891",
        "+1234567892",
        "+1234567893",
        "+1234567894"
    ]
    
    # Queue bulk calls
    task_id = celery_service.queue_bulk_calls(
        phone_numbers=phone_numbers,
        from_number=settings.twilio_phone_number,
        call_metadata={
            "campaign_id": "campaign_001",
            "call_type": "survey",
            "agent_id": "agent_002"
        },
        delay_between_calls=30,  # 30 seconds between calls
        max_concurrent_calls=3   # Max 3 concurrent calls
    )
    
    print(f"Queued bulk calls with task ID: {task_id}")
    print(f"Total calls to make: {len(phone_numbers)}")
    
    # Monitor the task
    monitor_task(task_id, "Bulk Calls")
    
    return task_id

def example_scheduled_call():
    """Example: Schedule a call for a specific time"""
    print("\n=== Scheduled Call Example ===")
    
    # Schedule call for 2 minutes from now
    schedule_time = (datetime.utcnow() + timedelta(minutes=2)).isoformat()
    
    task_id = celery_service.schedule_call(
        to_number="+1234567890",  # Replace with actual number
        from_number=settings.twilio_phone_number,
        schedule_time=schedule_time,
        call_metadata={
            "customer_id": "67890",
            "call_type": "appointment_reminder",
            "agent_id": "agent_003"
        }
    )
    
    print(f"Scheduled call with task ID: {task_id}")
    print(f"Call scheduled for: {schedule_time}")
    
    # Monitor the task
    monitor_task(task_id, "Scheduled Call")
    
    return task_id

def example_call_management():
    """Example: Call management operations"""
    print("\n=== Call Management Example ===")
    
    # First, make a call
    call_task_id = celery_service.queue_outbound_call(
        to_number="+1234567890",  # Replace with actual number
        from_number=settings.twilio_phone_number,
        call_metadata={"test_call": True}
    )
    
    print(f"Made test call with task ID: {call_task_id}")
    
    # Wait a bit for the call to be created
    time.sleep(5)
    
    # Get call status (this would need the actual call_sid from the call result)
    # For demonstration, we'll use a placeholder
    call_sid = "CA1234567890abcdef"  # This would come from the call result
    
    status_task_id = celery_service.get_call_status(call_sid)
    print(f"Getting call status with task ID: {status_task_id}")
    
    # End the call
    end_task_id = celery_service.end_call(call_sid)
    print(f"Ending call with task ID: {end_task_id}")
    
    return call_task_id, status_task_id, end_task_id

def example_retry_failed_call():
    """Example: Retry a failed call"""
    print("\n=== Retry Failed Call Example ===")
    
    # Simulate retrying a failed call
    original_task_id = "failed_task_123"  # This would be the actual failed task ID
    
    retry_task_id = celery_service.retry_failed_call(
        original_task_id=original_task_id,
        to_number="+1234567890",  # Replace with actual number
        from_number=settings.twilio_phone_number,
        retry_delay=300,  # 5 minutes delay
        call_metadata={
            "retry_attempt": 1,
            "original_task": original_task_id
        }
    )
    
    print(f"Retrying failed call with task ID: {retry_task_id}")
    print(f"Original failed task: {original_task_id}")
    
    # Monitor the task
    monitor_task(retry_task_id, "Retry Failed Call")
    
    return retry_task_id

def example_cleanup_operations():
    """Example: Cleanup operations"""
    print("\n=== Cleanup Operations Example ===")
    
    # Clean up stale calls (older than 12 hours)
    cleanup_task_id = celery_service.cleanup_stale_calls(max_age_hours=12)
    
    print(f"Cleanup stale calls with task ID: {cleanup_task_id}")
    
    # Monitor the task
    monitor_task(cleanup_task_id, "Cleanup Stale Calls")
    
    return cleanup_task_id

def monitor_task(task_id: str, task_name: str, max_wait: int = 60):
    """Monitor a task and show its progress"""
    print(f"\nMonitoring {task_name} (Task ID: {task_id})")
    print("-" * 50)
    
    start_time = time.time()
    last_status = None
    
    while time.time() - start_time < max_wait:
        try:
            # Get task status
            status = celery_service.get_task_status(task_id)
            current_status = status.get('status', 'UNKNOWN')
            
            # Only print if status changed
            if current_status != last_status:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Status: {current_status}")
                last_status = current_status
                
                # Show additional info if available
                if status.get('info'):
                    if isinstance(status['info'], dict):
                        progress = status['info'].get('progress', 0)
                        status_msg = status['info'].get('status', '')
                        if progress or status_msg:
                            print(f"  Progress: {progress}% - {status_msg}")
            
            # Check if task is complete
            if current_status in ['SUCCESS', 'FAILURE', 'REVOKED']:
                print(f"\nTask completed with status: {current_status}")
                
                if current_status == 'SUCCESS':
                    # Get the result
                    result = celery_service.get_task_result(task_id)
                    if result:
                        print("Task Result:")
                        for key, value in result.items():
                            print(f"  {key}: {value}")
                
                break
            
            time.sleep(2)  # Check every 2 seconds
            
        except Exception as e:
            print(f"Error monitoring task: {str(e)}")
            break
    
    else:
        print(f"Monitoring timeout after {max_wait} seconds")

def main():
    """Run all examples"""
    print("Twilio Celery Call Queuing Examples")
    print("=" * 50)
    
    # Check if Twilio is configured
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        print("⚠️  Warning: Twilio credentials not configured!")
        print("Please set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN environment variables")
        print("Examples will run but calls won't be made\n")
    
    try:
        # Run examples
        single_call_id = example_single_call()
        time.sleep(2)
        
        bulk_calls_id = example_bulk_calls()
        time.sleep(2)
        
        scheduled_call_id = example_scheduled_call()
        time.sleep(2)
        
        call_mgmt_ids = example_call_management()
        time.sleep(2)
        
        retry_call_id = example_retry_failed_call()
        time.sleep(2)
        
        cleanup_id = example_cleanup_operations()
        
        print("\n" + "=" * 50)
        print("All examples completed!")
        print("\nTask IDs for reference:")
        print(f"  Single Call: {single_call_id}")
        print(f"  Bulk Calls: {bulk_calls_id}")
        print(f"  Scheduled Call: {scheduled_call_id}")
        print(f"  Call Management: {call_mgmt_ids}")
        print(f"  Retry Call: {retry_call_id}")
        print(f"  Cleanup: {cleanup_id}")
        
        print("\nYou can monitor these tasks using:")
        print("  celery_service.get_task_status(task_id)")
        print("  celery_service.get_task_result(task_id)")
        
    except KeyboardInterrupt:
        print("\n\nExamples interrupted by user")
    except Exception as e:
        print(f"\nError running examples: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
