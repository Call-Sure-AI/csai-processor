"""
Celery Twilio Call Queuing API Routes
"""
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, Field, validator
from datetime import datetime, timezone
from loguru import logger

from services.celery_service import celery_service
from config.settings import settings

router = APIRouter()

# Pydantic Models for Request/Response

class CallMetadata(BaseModel):
    """Metadata for call tracking and organization"""
    customer_id: Optional[str] = Field(None, description="Customer ID for tracking")
    call_type: Optional[str] = Field(None, description="Type of call (e.g., follow_up, survey)")
    agent_id: Optional[str] = Field(None, description="Agent ID handling the call")
    campaign_id: Optional[str] = Field(None, description="Campaign ID for bulk calls")
    priority: Optional[str] = Field("normal", description="Call priority (low, normal, high)")
    custom_data: Optional[Dict[str, Any]] = Field(None, description="Additional custom data")
    message: Optional[str] = Field(None, description="Custom message to speak during the call")
    voice: Optional[str] = Field("alice", description="Voice to use (alice, man, woman)")
    language: Optional[str] = Field("en-US", description="Language code (en-US, en-GB, etc.)")
    gather_input: Optional[bool] = Field(False, description="Whether to gather user input during call")

class SingleCallRequest(BaseModel):
    """Request model for queuing a single call"""
    to_number: str = Field(..., description="Phone number to call (E.164 format)")
    from_number: Optional[str] = Field(None, description="Phone number to call from")
    webhook_url: Optional[str] = Field(None, description="Custom webhook URL for call handling")
    status_callback_url: Optional[str] = Field(None, description="Custom status callback URL")
    call_metadata: Optional[CallMetadata] = Field(None, description="Call metadata")
    delay_seconds: int = Field(0, ge=0, le=3600, description="Delay before making call (0-3600 seconds)")
    
    @validator('to_number')
    def validate_to_number(cls, v):
        if not v.startswith('+'):
            raise ValueError('Phone number must be in E.164 format (e.g., +1234567890)')
        return v
    
    @validator('from_number')
    def validate_from_number(cls, v):
        if v and not v.startswith('+'):
            raise ValueError('From number must be in E.164 format (e.g., +1234567890)')
        return v

class BulkCallRequest(BaseModel):
    """Request model for queuing bulk calls"""
    phone_numbers: List[str] = Field(..., min_items=1, max_items=1000, description="List of phone numbers to call")
    from_number: Optional[str] = Field(None, description="Phone number to call from")
    webhook_url: Optional[str] = Field(None, description="Custom webhook URL for call handling")
    status_callback_url: Optional[str] = Field(None, description="Custom status callback URL")
    call_metadata: Optional[CallMetadata] = Field(None, description="Call metadata for all calls")
    delay_between_calls: int = Field(5, ge=1, le=300, description="Seconds between calls (1-300)")
    max_concurrent_calls: int = Field(10, ge=1, le=50, description="Maximum concurrent calls (1-50)")
    
    @validator('phone_numbers')
    def validate_phone_numbers(cls, v):
        for number in v:
            if not number.startswith('+'):
                raise ValueError(f'Phone number {number} must be in E.164 format (e.g., +1234567890)')
        return v
    
    @validator('from_number')
    def validate_from_number(cls, v):
        if v and not v.startswith('+'):
            raise ValueError('From number must be in E.164 format (e.g., +1234567890)')
        return v

class ScheduledCallRequest(BaseModel):
    """Request model for scheduling a call"""
    to_number: str = Field(..., description="Phone number to call (E.164 format)")
    from_number: Optional[str] = Field(None, description="Phone number to call from")
    webhook_url: Optional[str] = Field(None, description="Custom webhook URL for call handling")
    status_callback_url: Optional[str] = Field(None, description="Custom status callback URL")
    call_metadata: Optional[CallMetadata] = Field(None, description="Call metadata")
    schedule_time: str = Field(..., description="ISO format datetime when to make the call")
    timezone: str = Field("Asia/Kolkata", description="Timezone for the schedule time")
    
    @validator('to_number')
    def validate_to_number(cls, v):
        if not v.startswith('+'):
            raise ValueError('Phone number must be in E.164 format (e.g., +1234567890)')
        return v
    
    @validator('from_number')
    def validate_from_number(cls, v):
        if v and not v.startswith('+'):
            raise ValueError('From number must be in E.164 format (e.g., +1234567890)')
        return v
    
    @validator('schedule_time')
    def validate_schedule_time(cls, v):
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('Schedule time must be in ISO format (e.g., 2024-01-15T10:00:00Z)')
        return v

class CallResponse(BaseModel):
    """Response model for call queuing operations"""
    success: bool = Field(..., description="Whether the operation was successful")
    task_id: str = Field(..., description="Celery task ID for tracking")
    message: str = Field(..., description="Human-readable message")
    created_at: str = Field(..., description="ISO format timestamp")
    estimated_completion: Optional[str] = Field(None, description="Estimated completion time")
    
    class Config:
        schema_extra = {
            "example": {
                "success": True,
                "task_id": "abc123-def456-ghi789",
                "message": "Call queued successfully",
                "created_at": "2024-01-15T10:00:00Z",
                "estimated_completion": "2024-01-15T10:05:00Z"
            }
        }

class BulkCallResponse(BaseModel):
    """Response model for bulk call queuing operations"""
    success: bool = Field(..., description="Whether the operation was successful")
    task_id: str = Field(..., description="Celery task ID for tracking")
    message: str = Field(..., description="Human-readable message")
    total_calls: int = Field(..., description="Total number of calls to be made")
    estimated_duration_minutes: int = Field(..., description="Estimated duration in minutes")
    created_at: str = Field(..., description="ISO format timestamp")
    
    class Config:
        schema_extra = {
            "example": {
                "success": True,
                "task_id": "abc123-def456-ghi789",
                "message": "Bulk call campaign queued successfully",
                "total_calls": 50,
                "estimated_duration_minutes": 25,
                "created_at": "2024-01-15T10:00:00Z"
            }
        }

class TaskStatusResponse(BaseModel):
    """Response model for task status"""
    task_id: str = Field(..., description="Task ID")
    status: str = Field(..., description="Task status (PENDING, PROGRESS, SUCCESS, FAILURE)")
    progress: Optional[int] = Field(None, description="Progress percentage (0-100)")
    message: Optional[str] = Field(None, description="Status message")
    result: Optional[Dict[str, Any]] = Field(None, description="Task result if completed")
    error: Optional[str] = Field(None, description="Error message if failed")
    created_at: str = Field(..., description="Task creation timestamp")
    updated_at: str = Field(..., description="Last update timestamp")

# API Endpoints

@router.post("/queue-single-call", response_model=CallResponse, tags=["Twilio Calls"])
async def queue_single_call(request: SingleCallRequest):
    """
    Queue a single outbound call using Celery
    
    This endpoint queues a single call to be processed asynchronously by the Celery worker.
    The call will be made using the configured Twilio service.
    """
    try:
        # Prepare call metadata
        metadata = {}
        if request.call_metadata:
            metadata = request.call_metadata.dict(exclude_none=True)
        
        # Queue the call
        task_id = celery_service.queue_outbound_call(
            to_number=request.to_number,
            from_number=request.from_number or settings.twilio_phone_number,
            webhook_url=request.webhook_url,
            status_callback_url=request.status_callback_url,
            call_metadata=metadata,
            delay_seconds=request.delay_seconds
        )
        
        # Calculate estimated completion
        estimated_completion = None
        if request.delay_seconds > 0:
            estimated_time = datetime.now().timestamp() + request.delay_seconds + 60  # Add 1 minute for processing
            estimated_completion = datetime.fromtimestamp(estimated_time).isoformat()
        
        logger.info(f"Queued single call to {request.to_number} with task ID: {task_id}")
        
        return CallResponse(
            success=True,
            task_id=task_id,
            message=f"Call to {request.to_number} queued successfully",
            created_at=datetime.now().isoformat(),
            estimated_completion=estimated_completion
        )
        
    except Exception as e:
        logger.error(f"Failed to queue single call: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to queue call: {str(e)}"
        )

@router.post("/queue-bulk-calls", response_model=BulkCallResponse, tags=["Twilio Calls"])
async def queue_bulk_calls(request: BulkCallRequest):
    """
    Queue multiple outbound calls using Celery
    
    This endpoint queues multiple calls to be processed asynchronously by the Celery worker.
    Calls will be made with rate limiting to respect Twilio's API limits.
    """
    try:
        # Prepare call metadata
        metadata = {}
        if request.call_metadata:
            metadata = request.call_metadata.dict(exclude_none=True)
        
        # Queue the bulk calls
        task_id = celery_service.queue_bulk_calls(
            phone_numbers=request.phone_numbers,
            from_number=request.from_number or settings.twilio_phone_number,
            webhook_url=request.webhook_url,
            status_callback_url=request.status_callback_url,
            call_metadata=metadata,
            delay_between_calls=request.delay_between_calls,
            max_concurrent_calls=request.max_concurrent_calls
        )
        
        # Calculate estimated duration
        total_delay = (len(request.phone_numbers) - 1) * request.delay_between_calls
        estimated_duration_minutes = max(1, total_delay // 60)
        
        logger.info(f"Queued bulk calls for {len(request.phone_numbers)} numbers with task ID: {task_id}")
        
        return BulkCallResponse(
            success=True,
            task_id=task_id,
            message=f"Bulk call campaign for {len(request.phone_numbers)} numbers queued successfully",
            total_calls=len(request.phone_numbers),
            estimated_duration_minutes=estimated_duration_minutes,
            created_at=datetime.now().isoformat()
        )
        
    except Exception as e:
        logger.error(f"Failed to queue bulk calls: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to queue bulk calls: {str(e)}"
        )

@router.post("/schedule-call", response_model=CallResponse, tags=["Twilio Calls"])
async def schedule_call(request: ScheduledCallRequest):
    """
    Schedule a call for a specific time using Celery
    
    This endpoint schedules a call to be made at a specific time using Celery's scheduling capabilities.
    """
    try:
        # Prepare call metadata
        metadata = {}
        if request.call_metadata:
            metadata = request.call_metadata.dict(exclude_none=True)
        
        # Schedule the call
        task_id = celery_service.schedule_call(
            to_number=request.to_number,
            from_number=request.from_number or settings.twilio_phone_number,
            webhook_url=request.webhook_url,
            status_callback_url=request.status_callback_url,
            call_metadata=metadata,
            schedule_time=request.schedule_time,
            timezone=request.timezone
        )
        
        logger.info(f"Scheduled call to {request.to_number} for {request.schedule_time} with task ID: {task_id}")
        
        return CallResponse(
            success=True,
            task_id=task_id,
            message=f"Call to {request.to_number} scheduled for {request.schedule_time}",
            created_at=datetime.now().isoformat(),
            estimated_completion=request.schedule_time
        )
        
    except Exception as e:
        logger.error(f"Failed to schedule call: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to schedule call: {str(e)}"
        )

@router.get("/task-status/{task_id}", response_model=TaskStatusResponse, tags=["Twilio Calls"])
async def get_task_status(task_id: str):
    """
    Get the status of a Celery task
    
    This endpoint returns the current status and progress of a queued call task.
    """
    try:
        # Get task status
        status = celery_service.get_task_status(task_id)
        
        # Get task result if completed successfully
        result = None
        if status.get('status') == 'SUCCESS':
            result = celery_service.get_task_result(task_id)
        
        return TaskStatusResponse(
            task_id=task_id,
            status=status.get('status', 'UNKNOWN'),
            progress=status.get('info', {}).get('progress') if isinstance(status.get('info'), dict) else None,
            message=status.get('info', {}).get('status') if isinstance(status.get('info'), dict) else None,
            result=result,
            error=status.get('traceback') if status.get('status') == 'FAILURE' else None,
            created_at=datetime.now().isoformat(),  # We don't have creation time from Celery
            updated_at=datetime.now().isoformat()
        )
        
    except Exception as e:
        logger.error(f"Failed to get task status for {task_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get task status: {str(e)}"
        )

@router.delete("/cancel-task/{task_id}", tags=["Twilio Calls"])
async def cancel_task(task_id: str):
    """
    Cancel a queued call task
    
    This endpoint cancels a pending or running call task.
    """
    try:
        success = celery_service.cancel_task(task_id)
        
        if success:
            logger.info(f"Task {task_id} cancelled successfully")
            return {
                "success": True,
                "message": f"Task {task_id} cancelled successfully",
                "task_id": task_id
            }
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Task {task_id} not found or could not be cancelled"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel task {task_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to cancel task: {str(e)}"
        )

@router.get("/active-tasks", tags=["Twilio Calls"])
async def list_active_tasks():
    """
    List all active Celery tasks
    
    This endpoint returns a list of all currently active (PENDING, PROGRESS) tasks
    in the Celery queue, including their status and basic information.
    """
    try:
        # Get active tasks from Celery service
        active_tasks = celery_service.get_active_tasks()
        
        logger.info(f"Retrieved {len(active_tasks)} active tasks")
        
        return {
            "success": True,
            "total_active_tasks": len(active_tasks),
            "tasks": active_tasks,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Failed to list active tasks: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list active tasks: {str(e)}"
        )

@router.post("/kill-all-tasks", tags=["Twilio Calls"])
async def kill_all_tasks():
    """
    Kill all running and pending Celery tasks
    
    This endpoint forcefully terminates all currently running and pending tasks
    in the Celery queue. Use with caution as this will cancel all ongoing operations.
    """
    try:
        # Kill all tasks using Celery service
        result = celery_service.kill_all_tasks()
        
        logger.warning(f"Killed {result['killed_count']} tasks")
        
        return {
            "success": True,
            "message": f"Successfully killed {result['killed_count']} tasks",
            "killed_count": result['killed_count'],
            "failed_count": result['failed_count'],
            "failed_task_ids": result['failed_task_ids'],
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Failed to kill all tasks: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to kill all tasks: {str(e)}"
        )

@router.get("/health", tags=["Twilio Calls"])
async def health_check():
    """
    Health check for Twilio call queuing service
    
    This endpoint checks if the Celery service and Twilio configuration are working properly.
    """
    try:
        # Check if Twilio is configured
        twilio_configured = all([
            settings.twilio_account_sid,
            settings.twilio_auth_token,
            settings.twilio_phone_number
        ])
        
        # Check Celery configuration
        celery_configured = all([
            settings.celery_broker_url,
            settings.celery_result_backend
        ])
        
        status = "healthy" if twilio_configured and celery_configured else "unhealthy"
        
        return {
            "status": status,
            "twilio_configured": twilio_configured,
            "celery_configured": celery_configured,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }
