from typing import Optional, Dict, List, Set
from datetime import datetime
from asyncio import Event, Lock, sleep, create_task
import asyncio
import logging
from twilio.rest import Client

from config.settings import settings
from services.campaign_service import CampaignService, CampaignCall

logger = logging.getLogger(__name__)


class CallQueueConfig:
    """Configuration for call queue"""
    def __init__(
        self,
        max_concurrent_calls: int = 5,
        delay_between_calls: int = 1  # seconds
    ):
        self.max_concurrent_calls = max_concurrent_calls
        self.delay_between_calls = delay_between_calls


class QueuedCampaign:
    """Queued campaign data"""
    def __init__(
        self,
        campaign_id: str,
        agent_id: Optional[str] = None,
        service: str = 'elevenlabs'
    ):
        self.campaign_id = campaign_id
        self.agent_id = agent_id
        self.service = service
        self.status = 'pending'
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None


class ActiveCall:
    """Active call tracking"""
    def __init__(
        self,
        call_sid: str,
        campaign_id: str,
        campaign_call_id: str,
        phone_number: str,
        start_time: datetime
    ):
        self.call_sid = call_sid
        self.campaign_id = campaign_id
        self.campaign_call_id = campaign_call_id
        self.phone_number = phone_number
        self.start_time = start_time


class CallQueueService:
    """Service for managing call queue and campaign execution"""
    
    _instance: Optional['CallQueueService'] = None
    _lock = Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, '_initialized'):
            self.config = CallQueueConfig(
                max_concurrent_calls=5,
                delay_between_calls=1
            )
            self.active_calls: Dict[str, ActiveCall] = {}
            self.campaign_queue: List[QueuedCampaign] = []
            self.processing_campaigns: Set[str] = set()
            self.is_processing = False
            self._shutdown_event = Event()
            self.twilio_client = Client(
                settings.twilio_account_sid,
                settings.twilio_auth_token
            )
            self._initialized = True
    
    @classmethod
    def get_instance(cls) -> 'CallQueueService':
        """Get singleton instance"""
        return cls()
    
    def configure(self, config: CallQueueConfig):
        """Update queue configuration"""
        self.config = config
        logger.info(
            f"CallQueue -> Configuration updated: "
            f"max_concurrent={config.max_concurrent_calls}, "
            f"delay={config.delay_between_calls}s"
        )
    
    async def add_campaign_to_queue(
        self,
        campaign_id: str,
        agent_id: Optional[str] = None,
        service: str = 'elevenlabs'
    ) -> Dict[str, any]:
        """Add campaign to processing queue"""
        try:
            # Check if campaign is already in queue or processing
            if campaign_id in self.processing_campaigns:
                logger.warning(f"CallQueue -> Campaign {campaign_id} is already in queue or processing")
                return {
                    'success': False,
                    'error': 'Campaign is already in queue or being processed'
                }
            
            # Validate campaign exists
            campaign = await CampaignService.get_campaign_by_id(campaign_id)
            if not campaign:
                return {
                    'success': False,
                    'error': 'Campaign not found'
                }
            
            # Add to queue
            queued_campaign = QueuedCampaign(
                campaign_id=campaign_id,
                agent_id=agent_id,
                service=service
            )
            
            self.campaign_queue.append(queued_campaign)
            self.processing_campaigns.add(campaign_id)
            
            logger.info(
                f"CallQueue -> Campaign {campaign_id} added to queue. "
                f"Queue length: {len(self.campaign_queue)}"
            )
            
            # Start processing if not already running
            if not self.is_processing:
                create_task(self._process_queue())
            
            return {
                'success': True,
                'campaign_id': campaign_id,
                'queue_position': len(self.campaign_queue)
            }
            
        except Exception as e:
            logger.error(f"CallQueue -> Error adding campaign to queue: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def pause_campaign(self, campaign_id: str) -> bool:
        """Pause campaign execution"""
        try:
            # Find campaign in queue
            for campaign in self.campaign_queue:
                if campaign.campaign_id == campaign_id:
                    campaign.status = 'paused'
                    logger.info(f"CallQueue -> Campaign {campaign_id} paused")
                    
                    # Update database status
                    await CampaignService.update_campaign_status(campaign_id, 'paused')
                    return True
            
            logger.warning(f"CallQueue -> Campaign {campaign_id} not found in queue")
            return False
            
        except Exception as e:
            logger.error(f"CallQueue -> Error pausing campaign: {e}")
            return False
    
    async def resume_campaign(self, campaign_id: str) -> bool:
        """Resume paused campaign"""
        try:
            # Find paused campaign
            for campaign in self.campaign_queue:
                if campaign.campaign_id == campaign_id and campaign.status == 'paused':
                    campaign.status = 'pending'
                    logger.info(f"CallQueue -> Campaign {campaign_id} resumed")
                    
                    # Update database status
                    await CampaignService.update_campaign_status(campaign_id, 'active')
                    
                    # Restart processing if needed
                    if not self.is_processing:
                        create_task(self._process_queue())
                    
                    return True
            
            logger.warning(f"CallQueue -> Campaign {campaign_id} not found or not paused")
            return False
            
        except Exception as e:
            logger.error(f"CallQueue -> Error resuming campaign: {e}")
            return False
    
    async def _process_queue(self):
        """Main queue processing loop"""
        if self.is_processing:
            return
        
        self.is_processing = True
        logger.info("CallQueue -> Starting queue processor")
        
        try:
            while self.campaign_queue and not self._shutdown_event.is_set():
                # Get next campaign
                campaign = self.campaign_queue[0]
                
                # Skip if paused
                if campaign.status == 'paused':
                    await sleep(1)
                    continue
                
                # Process campaign
                campaign.status = 'processing'
                campaign.start_time = datetime.now()
                
                logger.info(f"CallQueue -> Processing campaign {campaign.campaign_id}")
                
                try:
                    await self._process_campaign(campaign)
                    
                    # Mark campaign as completed
                    campaign.status = 'completed'
                    campaign.end_time = datetime.now()
                    
                    # Remove from queue and processing set
                    self.campaign_queue.pop(0)
                    self.processing_campaigns.discard(campaign.campaign_id)
                    
                    logger.info(
                        f"CallQueue -> Campaign {campaign.campaign_id} completed. "
                        f"Remaining in queue: {len(self.campaign_queue)}"
                    )
                    
                except Exception as e:
                    logger.error(f"CallQueue -> Error processing campaign {campaign.campaign_id}: {e}")
                    campaign.status = 'failed'
                    campaign.end_time = datetime.now()
                    
                    # Remove from queue and processing set
                    self.campaign_queue.pop(0)
                    self.processing_campaigns.discard(campaign.campaign_id)
                    
                    # Update database status
                    await CampaignService.update_campaign_status(campaign.campaign_id, 'failed')
        
        finally:
            self.is_processing = False
            logger.info("CallQueue -> Queue processor stopped")
    
    async def _process_campaign(self, campaign: QueuedCampaign):
        """Process a single campaign"""
        try:
            # Update campaign status to active
            await CampaignService.update_campaign_status(campaign.campaign_id, 'active')
            
            # Process calls until campaign is complete
            while not await CampaignService.is_campaign_complete(campaign.campaign_id):
                # Check if paused
                if campaign.status == 'paused':
                    logger.info(f"CallQueue -> Campaign {campaign.campaign_id} is paused")
                    break
                
                # Wait if at max concurrent calls
                while len(self.active_calls) >= self.config.max_concurrent_calls:
                    await sleep(0.5)
                
                # Get next pending call
                next_call = await CampaignService.get_next_pending_call(campaign.campaign_id)
                
                if not next_call:
                    # No pending calls, wait a bit and check again
                    await sleep(1)
                    continue
                
                # Make the call
                await self._make_campaign_call(
                    campaign_call=next_call,
                    agent_id=campaign.agent_id,
                    service=campaign.service
                )
                
                # Delay between calls
                await sleep(self.config.delay_between_calls)
            
            # Mark campaign as completed if all calls are done
            if await CampaignService.is_campaign_complete(campaign.campaign_id):
                await CampaignService.mark_campaign_completed(campaign.campaign_id)
                logger.info(f"CallQueue -> Campaign {campaign.campaign_id} marked as completed")
            
        except Exception as e:
            logger.error(f"CallQueue -> Error in campaign processing: {e}")
            raise
    
    async def _make_campaign_call(
        self,
        campaign_call: CampaignCall,
        agent_id: Optional[str],
        service: str
    ):
        """Make a single campaign call"""
        try:
            # Build webhook URL
            url = f"https://{settings.server_url.strip()}/incoming"
            url += f"?service={service}&isOutbound=true&campaignCallId={campaign_call.id}"
            
            if agent_id:
                url += f"&agent_id={agent_id}"
            
            # Update call status to calling
            await CampaignService.update_campaign_call_status(
                campaign_call.id,
                'calling'
            )
            
            # Make Twilio call
            full_number = f"{campaign_call.country_code}{campaign_call.phone_number}"
            
            logger.info(
                f"CallQueue -> Initiating call to {full_number} "
                f"for campaign {campaign_call.campaign_id}"
            )
            
            call = self.twilio_client.calls.create(
                url=url,
                to=full_number,
                from_=settings.from_number,
                status_callback=f"https://{settings.server_url.strip()}/campaign/call-status",
                status_callback_event=['initiated', 'ringing', 'answered', 'completed']
            )
            
            # Track active call
            active_call = ActiveCall(
                call_sid=call.sid,
                campaign_id=campaign_call.campaign_id,
                campaign_call_id=campaign_call.id,
                phone_number=full_number,
                start_time=datetime.now()
            )
            
            self.active_calls[call.sid] = active_call
            
            # Update call status with SID
            await CampaignService.update_campaign_call_status(
                campaign_call.id,
                'calling',
                call_sid=call.sid
            )
            
            logger.info(
                f"CallQueue -> Call initiated: {call.sid} to {full_number}. "
                f"Active calls: {len(self.active_calls)}"
            )
            
        except Exception as e:
            logger.error(f"CallQueue -> Error making campaign call: {e}")
            
            # Update call status to failed
            await CampaignService.update_campaign_call_status(
                campaign_call.id,
                'failed',
                error_message=str(e)
            )
            
            raise
    
    async def handle_call_status_update(
        self,
        call_sid: str,
        status: str,
        duration: Optional[int] = None
    ):
        """Handle Twilio call status callback"""
        try:
            if call_sid not in self.active_calls:
                logger.warning(f"CallQueue -> Received status for unknown call: {call_sid}")
                return
            
            active_call = self.active_calls[call_sid]
            
            logger.info(
                f"CallQueue -> Call status update: {call_sid} -> {status} "
                f"(Campaign: {active_call.campaign_id})"
            )
            
            # Update campaign call status
            if status in ['completed', 'failed', 'busy', 'no-answer', 'canceled']:
                await CampaignService.update_campaign_call_status(
                    active_call.campaign_call_id,
                    status,
                    duration=duration
                )
                
                # Remove from active calls
                del self.active_calls[call_sid]
                
                logger.info(
                    f"CallQueue -> Call {call_sid} finished. "
                    f"Active calls: {len(self.active_calls)}"
                )
            
        except Exception as e:
            logger.error(f"CallQueue -> Error handling call status update: {e}")
    
    def get_queue_status(self) -> Dict[str, any]:
        """Get current queue status"""
        return {
            'is_processing': self.is_processing,
            'queued_campaigns': len(self.campaign_queue),
            'active_calls': len(self.active_calls),
            'max_concurrent_calls': self.config.max_concurrent_calls,
            'campaigns': [
                {
                    'campaign_id': c.campaign_id,
                    'status': c.status,
                    'start_time': c.start_time.isoformat() if c.start_time else None
                }
                for c in self.campaign_queue
            ],
            'active_call_details': [
                {
                    'call_sid': c.call_sid,
                    'campaign_id': c.campaign_id,
                    'phone_number': c.phone_number,
                    'duration': (datetime.now() - c.start_time).total_seconds()
                }
                for c in self.active_calls.values()
            ]
        }
    
    async def shutdown(self):
        """Gracefully shutdown the queue service"""
        logger.info("CallQueue -> Shutting down...")
        self._shutdown_event.set()
        
        # Wait for processing to stop
        while self.is_processing:
            await sleep(0.5)
        
        logger.info("CallQueue -> Shutdown complete")
