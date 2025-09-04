"""
Twilio Call Tasks for Celery
"""
import time
import asyncio
from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timedelta, timezone
from celery import current_task
from loguru import logger
from .base import BaseTask
from services.celery_app import celery_app
from services.voice.twilio_service import TwilioVoiceService
from config.settings import settings
from sqlalchemy.orm import Session
from database.config import SessionLocal
from database.models import CeleryTaskMap

# Global Twilio service instance
twilio_service: Optional[TwilioVoiceService] = None

async def get_twilio_service() -> TwilioVoiceService:
    """Get or initialize Twilio service instance"""
    global twilio_service
    if not twilio_service:
        twilio_service = TwilioVoiceService()
        await twilio_service.initialize()
    return twilio_service

@celery_app.task(base=BaseTask, bind=True)
def queue_outbound_call_task(
    self,
    to_number: str,
    from_number: Optional[str] = None,
    webhook_url: str = None,
    status_callback_url: Optional[str] = None,
    call_metadata: Optional[Dict[str, Any]] = None,
    delay_seconds: int = 0,
    max_retries: int = 3,
    **kwargs
) -> Dict[str, Any]:
    """
    Queue an outbound call using Twilio
    
    Args:
        to_number: Phone number to call
        from_number: Phone number to call from (uses default if not provided)
        webhook_url: TwiML webhook URL for call handling
        status_callback_url: URL for call status updates
        call_metadata: Additional metadata for the call
        delay_seconds: Delay before making the call
        max_retries: Maximum retry attempts for failed calls
    """
    try:
        current_task.update_state(
            state='PROGRESS',
            meta={'status': 'Initializing call', 'progress': 10}
        )
        
        # Remove this delay block since we're using countdown
        # if delay_seconds > 0:
        #     logger.info(f"Delaying call to {to_number} by {delay_seconds} seconds")
        #     time.sleep(delay_seconds)
        
        current_task.update_state(
            state='PROGRESS',
            meta={'status': 'Getting Twilio service', 'progress': 20}
        )
        
        # Get Twilio service
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            twilio_svc = loop.run_until_complete(get_twilio_service())
            
            if not twilio_svc.client:
                raise RuntimeError("Twilio service not initialized")
            
            current_task.update_state(
                state='PROGRESS',
                meta={'status': 'Creating call', 'progress': 50}
            )
            
            # Fix webhook URL construction
            if not webhook_url:
                if not settings.webhook_base_url:
                    raise ValueError("Webhook URL or webhook_base_url must be configured")
                webhook_url = f"{settings.webhook_base_url}/api/v1/twilio/incoming-call"
            
            # Add custom message parameters to webhook URL if provided
            if call_metadata:
                from urllib.parse import urlencode
                params = {
                    'voice': call_metadata.get('voice', 'alice'),
                    'language': call_metadata.get('language', 'en-US'),
                    'gather_input': str(call_metadata.get('gather_input', True)).lower()  # Default to True for chat
                }
                
                # Add message if provided
                if call_metadata.get('message'):
                    params['message'] = call_metadata.get('message')
                
                # Add custom data if provided (for personalized messages)
                if call_metadata.get('custom_data'):
                    import json
                    params['custom_data'] = json.dumps(call_metadata.get('custom_data'))
                    logger.info(f"Custom data: {params['custom_data']}")
                
                webhook_url_with_params = f"{webhook_url}?{urlencode(params)}"
            else:
                # Default parameters for chat functionality
                from urllib.parse import urlencode
                params = {
                    'voice': 'alice',
                    'language': 'en-US',
                    'gather_input': 'true'
                }
                webhook_url_with_params = f"{webhook_url}?{urlencode(params)}"
            
            logger.info(f"Webhook URL: {webhook_url}")

            logger.info(f"Webhook URL with params: {webhook_url_with_params}")
            
            # Create the call
            call_data = loop.run_until_complete(
                twilio_svc.create_call(
                    to_number=to_number,
                    from_number=from_number or settings.twilio_phone_number,
                    webhook_url=webhook_url_with_params,
                    status_callback_url=status_callback_url,
                    **kwargs
                )
            )
            db: Session = SessionLocal()
            try:
                rec = db.query(CeleryTaskMap).filter(CeleryTaskMap.task_id == self.request.id).one_or_none()
                if rec:
                    rec.call_sid = call_data['call_sid']
                    rec.success = True
                    db.commit()
            finally:
                db.close()
        finally:
            loop.close()
        
        current_task.update_state(
            state='PROGRESS',
            meta={'status': 'Call created successfully', 'progress': 90}
        )
        
        result = {
            'success': True,
            'call_sid': call_data['call_sid'],
            'status': call_data['status'],
            'to_number': to_number,
            'from_number': call_data['from'],
            'created_at': call_data['created_at'].isoformat(),
            'metadata': call_metadata or {},
            'task_id': self.request.id,
            'unique_call_id': f"{self.request.id}_{to_number}_{int(datetime.utcnow().timestamp())}"
        }
        
        logger.info(f"Successfully queued outbound call: {call_data['call_sid']} to {to_number}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to queue outbound call to {to_number}: {str(e)}")
        raise

@celery_app.task(base=BaseTask, bind=True)
def queue_bulk_calls_task(
    self,
    phone_numbers: List[str],
    from_number: Optional[str] = None,
    webhook_url: str = None,
    status_callback_url: Optional[str] = None,
    call_metadata: Optional[Dict[str, Any]] = None,
    delay_between_calls: int = 5,
    max_concurrent_calls: int = 10,
    **kwargs
) -> Dict[str, Any]:
    """
    Queue multiple outbound calls with rate limiting
    
    Args:
        phone_numbers: List of phone numbers to call
        from_number: Phone number to call from
        webhook_url: TwiML webhook URL for call handling
        status_callback_url: URL for call status updates
        call_metadata: Additional metadata for the calls
        delay_between_calls: Seconds to wait between calls
        max_concurrent_calls: Maximum concurrent calls allowed
    """
    try:
        # Validate input
        if not phone_numbers:
            raise ValueError("Phone numbers list cannot be empty")
        
        if not settings.twilio_account_sid or not settings.twilio_auth_token:
            raise RuntimeError("Twilio credentials not configured")
        
        total_calls = len(phone_numbers)
        successful_calls = []
        failed_calls = []
        
        current_task.update_state(
            state='PROGRESS',
            meta={
                'status': f'Starting bulk call campaign ({total_calls} numbers)',
                'progress': 0,
                'total': total_calls,
                'completed': 0,
                'successful': 0,
                'failed': 0
            }
        )
        
        # Queue individual calls with delays
        for i, phone_number in enumerate(phone_numbers):
            try:
                # Calculate delay for this call
                call_delay = i * delay_between_calls
                
                # Queue the individual call task directly
                call_task = queue_outbound_call_task.apply_async(
                    kwargs={
                        'to_number': phone_number,
                        'from_number': from_number or settings.twilio_phone_number,
                        'webhook_url': webhook_url,
                        'status_callback_url': status_callback_url,
                        'call_metadata': call_metadata,
                        'delay_seconds': 0,  # Set to 0, use countdown instead
                        **kwargs
                    },
                    countdown=call_delay  # Use countdown for proper scheduling
                )
                
                successful_calls.append({
                    'phone_number': phone_number,
                    'task_id': call_task.id,
                    'scheduled_delay': call_delay
                })
                
                logger.info(f"Queued call {i+1}/{total_calls} to {phone_number} (delay: {call_delay}s, task_id: {call_task.id}, scheduled for: {datetime.now() + timedelta(seconds=call_delay)})")
                
            except Exception as e:
                failed_calls.append({
                    'phone_number': phone_number,
                    'error': str(e)
                })
                logger.error(f"Failed to queue call to {phone_number}: {str(e)}")
            
            # Update progress
            progress = int((i + 1) / total_calls * 100)
            current_task.update_state(
                state='PROGRESS',
                meta={
                    'status': f'Queued {i+1}/{total_calls} calls',
                    'progress': progress,
                    'total': total_calls,
                    'completed': i + 1,
                    'successful': len(successful_calls),
                    'failed': len(failed_calls)
                }
            )
        
        result = {
            'success': True,
            'total_calls': total_calls,
            'successful_calls': len(successful_calls),
            'failed_calls': len(failed_calls),
            'successful_call_details': successful_calls,
            'failed_call_details': failed_calls,
            'campaign_id': f"bulk_{self.request.id}",
            'created_at': datetime.utcnow().isoformat()
        }
        
        logger.info(f"Bulk call campaign completed: {len(successful_calls)}/{total_calls} calls queued successfully")
        return result
        
    except Exception as e:
        logger.error(f"Failed to queue bulk calls: {str(e)}")
        raise

@celery_app.task(base=BaseTask, bind=True)
def schedule_call_task(
    self,
    to_number: str,
    from_number: Optional[str] = None,
    webhook_url: str = None,
    status_callback_url: Optional[str] = None,
    call_metadata: Optional[Dict[str, Any]] = None,
    schedule_time: Optional[str] = None,  # ISO format datetime string
    timezone: str = "UTC",
    **kwargs
) -> Dict[str, Any]:
    """
    Schedule a call for a specific time
    
    Args:
        to_number: Phone number to call
        from_number: Phone number to call from
        webhook_url: TwiML webhook URL for call handling
        status_callback_url: URL for call status updates
        call_metadata: Additional metadata for the call
        schedule_time: When to make the call (ISO format)
        timezone: Timezone for the schedule time
    """
    try:
        if schedule_time:
            # Parse schedule time and calculate delay
            schedule_dt = datetime.fromisoformat(schedule_time.replace('Z', '+00:00'))
            now = datetime.utcnow()
            
            if schedule_dt.tzinfo is None:
                schedule_dt = schedule_dt.replace(tzinfo=timezone.utc)
            
            delay_seconds = max(0, int((schedule_dt - now).total_seconds()))
            
            logger.info(f"Scheduling call to {to_number} for {schedule_time} (delay: {delay_seconds}s)")
            
            # Queue the call with delay
            call_task = queue_outbound_call_task.apply_async(
                kwargs={
                    'to_number': to_number,
                    'from_number': from_number,
                    'webhook_url': webhook_url,
                    'status_callback_url': status_callback_url,
                    'call_metadata': call_metadata,
                    'delay_seconds': 0,  # Delay handled by countdown
                    **kwargs
                },
                countdown=delay_seconds
            )
            
            result = {
                'success': True,
                'scheduled_time': schedule_time,
                'delay_seconds': delay_seconds,
                'call_task_id': call_task.id,
                'schedule_task_id': self.request.id,
                'to_number': to_number,
                'created_at': datetime.utcnow().isoformat()
            }
        else:
            # No schedule time, make call immediately
            result = queue_outbound_call_task.apply_async(
                kwargs={
                    'to_number': to_number,
                    'from_number': from_number,
                    'webhook_url': webhook_url,
                    'status_callback_url': status_callback_url,
                    'call_metadata': call_metadata,
                    **kwargs
                }
            ).get()
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to schedule call to {to_number}: {str(e)}")
        raise

@celery_app.task(base=BaseTask, bind=True)
def retry_failed_call_task(
    self,
    original_task_id: str,
    to_number: str,
    from_number: Optional[str] = None,
    webhook_url: str = None,
    status_callback_url: Optional[str] = None,
    call_metadata: Optional[Dict[str, Any]] = None,
    retry_delay: int = 300,  # 5 minutes
    **kwargs
) -> Dict[str, Any]:
    """
    Retry a failed call after a delay
    
    Args:
        original_task_id: ID of the original failed task
        to_number: Phone number to call
        from_number: Phone number to call from
        webhook_url: TwiML webhook URL for call handling
        status_callback_url: URL for call status updates
        call_metadata: Additional metadata for the call
        retry_delay: Seconds to wait before retry
    """
    try:
        logger.info(f"Retrying failed call to {to_number} (original task: {original_task_id})")
        
        # Queue the retry call
        call_task = queue_outbound_call_task.apply_async(
            kwargs={
                'to_number': to_number,
                'from_number': from_number,
                'webhook_url': webhook_url,
                'status_callback_url': status_callback_url,
                'call_metadata': call_metadata,
                **kwargs
            },
            countdown=retry_delay
        )
        
        result = {
            'success': True,
            'original_task_id': original_task_id,
            'retry_task_id': call_task.id,
            'retry_delay': retry_delay,
            'to_number': to_number,
            'retry_attempt': self.request.retries + 1,
            'created_at': datetime.utcnow().isoformat()
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to retry call to {to_number}: {str(e)}")
        raise

@celery_app.task(base=BaseTask, bind=True)
def end_call_task(
    self,
    call_sid: str,
    **kwargs
) -> Dict[str, Any]:
    """
    End an active call
    
    Args:
        call_sid: Twilio call SID to end
    """
    try:
        current_task.update_state(
            state='PROGRESS',
            meta={'status': 'Getting Twilio service', 'progress': 20}
        )
        
        # Get Twilio service
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            twilio_svc = loop.run_until_complete(get_twilio_service())
            
            current_task.update_state(
                state='PROGRESS',
                meta={'status': 'Ending call', 'progress': 60}
            )
            
            # End the call
            success = loop.run_until_complete(twilio_svc.end_call(call_sid))
        finally:
            loop.close()
        
        result = {
            'success': success,
            'call_sid': call_sid,
            'ended_at': datetime.utcnow().isoformat(),
            'task_id': self.request.id
        }
        
        if success:
            logger.info(f"Successfully ended call: {call_sid}")
        else:
            logger.warning(f"Failed to end call: {call_sid}")
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to end call {call_sid}: {str(e)}")
        raise

@celery_app.task(base=BaseTask, bind=True)
def cleanup_stale_calls_task(
    self,
    max_age_hours: int = 24,
    **kwargs
) -> Dict[str, Any]:
    """
    Clean up stale call records
    
    Args:
        max_age_hours: Maximum age of call records to keep
    """
    try:
        current_task.update_state(
            state='PROGRESS',
            meta={'status': 'Getting Twilio service', 'progress': 20}
        )
        
        # Get Twilio service
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            twilio_svc = loop.run_until_complete(get_twilio_service())
            
            current_task.update_state(
                state='PROGRESS',
                meta={'status': 'Cleaning up stale calls', 'progress': 60}
            )
            
            # Clean up stale calls
            loop.run_until_complete(twilio_svc.cleanup_stale_calls(max_age_hours))
            
            active_calls_count = twilio_svc.get_active_calls_count()
        finally:
            loop.close()
        
        result = {
            'success': True,
            'max_age_hours': max_age_hours,
            'active_calls_remaining': active_calls_count,
            'cleaned_at': datetime.utcnow().isoformat(),
            'task_id': self.request.id
        }
        
        logger.info(f"Cleaned up stale calls (max age: {max_age_hours}h), {active_calls_count} active calls remaining")
        return result
        
    except Exception as e:
        logger.error(f"Failed to cleanup stale calls: {str(e)}")
        raise

@celery_app.task(base=BaseTask, bind=True)
def get_call_status_task(
    self,
    call_sid: str,
    **kwargs
) -> Dict[str, Any]:
    """
    Get status of a specific call
    
    Args:
        call_sid: Twilio call SID to check
    """
    try:
        current_task.update_state(
            state='PROGRESS',
            meta={'status': 'Getting Twilio service', 'progress': 20}
        )
        
        # Get Twilio service
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            twilio_svc = loop.run_until_complete(get_twilio_service())
            
            current_task.update_state(
                state='PROGRESS',
                meta={'status': 'Getting call status', 'progress': 60}
            )
            
            # Get call info
            call_info = twilio_svc.get_call_info(call_sid)
        finally:
            loop.close()
        
        result = {
            'success': True,
            'call_sid': call_sid,
            'call_info': call_info,
            'checked_at': datetime.utcnow().isoformat(),
            'task_id': self.request.id
        }
        
        if call_info:
            logger.info(f"Retrieved call status for {call_sid}: {call_info.get('status', 'unknown')}")
        else:
            logger.warning(f"Call {call_sid} not found in active calls")
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to get call status for {call_sid}: {str(e)}")
        raise
