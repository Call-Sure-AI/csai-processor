from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response
from typing import Optional, Dict, Any
import logging
import json
from datetime import datetime

from services.campaign_service import CampaignService
from services.csv_parser_service import CSVParserService
from services.call_queue_service import CallQueueService
from config.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)

# Initialize call queue service
call_queue = CallQueueService.get_instance()


@router.post("/trigger")
async def trigger_campaign(request: Request):
    """
    Trigger campaign execution
    POST /campaign/trigger
    Body: { campaignId, agentId?, service? }
    """
    try:
        data = await request.json()
        campaign_id = data.get('campaignId')
        agent_id = data.get('agentId')
        service = data.get('service', 'elevenlabs')
        
        if not campaign_id:
            raise HTTPException(status_code=400, detail="campaignId is required")
        
        if service not in ['deepgram', 'elevenlabs']:
            raise HTTPException(
                status_code=400,
                detail="service must be either 'deepgram' or 'elevenlabs'"
            )
        
        # Add campaign to queue
        result = await call_queue.add_campaign_to_queue(
            campaign_id=campaign_id,
            agent_id=agent_id,
            service=service
        )
        
        if not result['success']:
            raise HTTPException(status_code=400, detail=result.get('error', 'Failed to queue campaign'))
        
        logger.info(f"Campaign {campaign_id} triggered with {service} service")
        
        return JSONResponse(
            status_code=200,
            content={
                "message": "Campaign queued successfully",
                "campaignId": campaign_id,
                "queuePosition": result.get('queue_position'),
                "service": service
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering campaign: {e}")
        raise HTTPException(status_code=500, detail="Failed to trigger campaign")


@router.get("/status/{campaign_id}")
async def get_campaign_status(campaign_id: str):
    """
    Get campaign status
    GET /campaign/status/:campaignId
    """
    try:
        status = await CampaignService.get_campaign_status(campaign_id)
        
        if not status:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        return JSONResponse(
            status_code=200,
            content={
                "campaignId": status.campaign_id,
                "status": status.status,
                "totalLeads": status.total_leads,
                "completedCalls": status.completed_calls,
                "failedCalls": status.failed_calls,
                "pendingCalls": status.pending_calls,
                "successRate": status.success_rate,
                "estimatedCompletionTime": (
                    status.estimated_completion_time.isoformat()
                    if status.estimated_completion_time
                    else None
                )
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting campaign status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get campaign status")


@router.post("/upload-csv")
async def upload_csv(
    campaign_id: str = Form(...),
    file_url: str = Form(...),
    phone_number_column: str = Form(...),
    country_code_column: str = Form(...)
):
    """
    Upload CSV for campaign leads
    POST /campaign/upload-csv
    Form data: campaignId, fileUrl, phoneNumberColumn, countryCodeColumn
    """
    try:
        # Validate campaign exists
        campaign = await CampaignService.get_campaign_by_id(campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # Build data mapping
        data_mapping = {
            'phone_number_column': phone_number_column,
            'country_code_column': country_code_column
        }
        
        # Validate CSV structure
        validation = await CSVParserService.validate_csv_structure(file_url, data_mapping)
        
        if not validation['is_valid']:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "errors": validation['errors']
                }
            )
        
        # Parse CSV and extract leads
        leads = await CSVParserService.parse_csv_from_url(file_url, data_mapping)
        
        if not leads:
            raise HTTPException(status_code=400, detail="No valid leads found in CSV")
        
        # Create campaign calls
        success = await CampaignService.create_campaign_calls(campaign_id, leads)
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to create campaign calls")
        
        logger.info(f"Uploaded {len(leads)} leads for campaign {campaign_id}")
        
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "campaignId": campaign_id,
                "leadsCount": len(leads),
                "sampleData": validation.get('sample_data', [])[:3]  # First 3 samples
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading CSV: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload CSV: {str(e)}")


@router.post("/pause/{campaign_id}")
async def pause_campaign(campaign_id: str):
    """
    Pause campaign execution
    POST /campaign/pause/:campaignId
    """
    try:
        success = await call_queue.pause_campaign(campaign_id)
        
        if not success:
            raise HTTPException(
                status_code=404,
                detail="Campaign not found in queue or already paused"
            )
        
        logger.info(f"Campaign {campaign_id} paused")
        
        return JSONResponse(
            status_code=200,
            content={
                "message": "Campaign paused successfully",
                "campaignId": campaign_id
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error pausing campaign: {e}")
        raise HTTPException(status_code=500, detail="Failed to pause campaign")


@router.post("/resume/{campaign_id}")
async def resume_campaign(campaign_id: str):
    """
    Resume paused campaign
    POST /campaign/resume/:campaignId
    """
    try:
        success = await call_queue.resume_campaign(campaign_id)
        
        if not success:
            raise HTTPException(
                status_code=404,
                detail="Campaign not found in queue or not paused"
            )
        
        logger.info(f"Campaign {campaign_id} resumed")
        
        return JSONResponse(
            status_code=200,
            content={
                "message": "Campaign resumed successfully",
                "campaignId": campaign_id
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resuming campaign: {e}")
        raise HTTPException(status_code=500, detail="Failed to resume campaign")


@router.post("/call-status")
async def campaign_call_status(
    CallSid: str = Form(...),
    CallStatus: str = Form(...),
    CallDuration: Optional[str] = Form(None)
):
    """
    Webhook for campaign call status updates
    POST /campaign/call-status
    Called by Twilio with call status updates
    """
    try:
        duration = int(CallDuration) if CallDuration else None
        
        await call_queue.handle_call_status_update(
            call_sid=CallSid,
            status=CallStatus,
            duration=duration
        )
        
        return Response(status_code=200)
        
    except Exception as e:
        logger.error(f"Error processing call status: {e}")
        return Response(status_code=500)


@router.get("/queue-status")
async def get_queue_status():
    """
    Get current call queue status
    GET /campaign/queue-status
    """
    try:
        status = call_queue.get_queue_status()
        return JSONResponse(status_code=200, content=status)
        
    except Exception as e:
        logger.error(f"Error getting queue status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get queue status")
