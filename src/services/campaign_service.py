from typing import Optional, List, Dict, Any
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.orm import Session
import logging

from database.config import db, get_db

logger = logging.getLogger(__name__)


class Campaign:
    """Campaign data model"""
    def __init__(self, data: Dict[str, Any]):
        self.id = data.get('id')
        self.campaign_name = data.get('campaign_name')
        self.description = data.get('description')
        self.company_id = data.get('company_id')
        self.created_by = data.get('created_by')
        self.status = data.get('status')
        self.leads_count = data.get('leads_count', 0)
        self.csv_file_path = data.get('csv_file_path')
        self.data_mapping = data.get('data_mapping')
        self.booking_config = data.get('booking_config')
        self.automation_config = data.get('automation_config')
        self.email_settings = data.get('email_settings')
        self.call_settings = data.get('call_settings')
        self.schedule_settings = data.get('schedule_settings')
        self.leads_file_url = data.get('leads_file_url')
        self.created_at = data.get('created_at')
        self.updated_at = data.get('updated_at')


class CampaignCall:
    """Campaign call data model"""
    def __init__(self, data: Dict[str, Any]):
        self.id = data.get('id')
        self.campaign_id = data.get('campaign_id')
        self.phone_number = data.get('phone_number')
        self.country_code = data.get('country_code')
        self.status = data.get('status')
        self.call_sid = data.get('call_sid')
        self.duration = data.get('duration')
        self.cost = data.get('cost')
        self.error_message = data.get('error_message')
        self.retry_count = data.get('retry_count', 0)
        self.max_retries = data.get('max_retries', 3)
        self.scheduled_at = data.get('scheduled_at')
        self.started_at = data.get('started_at')
        self.completed_at = data.get('completed_at')
        self.created_at = data.get('created_at')
        self.updated_at = data.get('updated_at')


class LeadData:
    """Lead data structure"""
    def __init__(self, phone_number: str, country_code: str, **kwargs):
        self.phone_number = phone_number
        self.country_code = country_code
        self.extra_data = kwargs


class CampaignStatusResponse:
    """Campaign status response"""
    def __init__(
        self,
        campaign_id: str,
        status: str,
        total_leads: int,
        completed_calls: int,
        failed_calls: int,
        pending_calls: int,
        success_rate: float,
        estimated_completion_time: Optional[datetime] = None
    ):
        self.campaign_id = campaign_id
        self.status = status
        self.total_leads = total_leads
        self.completed_calls = completed_calls
        self.failed_calls = failed_calls
        self.pending_calls = pending_calls
        self.success_rate = success_rate
        self.estimated_completion_time = estimated_completion_time


class CampaignService:
    """Service for managing campaigns and campaign calls"""
    
    @staticmethod
    async def get_campaign_by_id(campaign_id: str) -> Optional[Campaign]:
        """Get campaign by ID"""
        try:
            query = text("""
                SELECT
                    id, campaign_name, description, company_id, created_by, status,
                    leads_count, csv_file_path, data_mapping, booking_config,
                    automation_config, email_settings, call_settings, schedule_settings,
                    leads_file_url, created_at, updated_at
                FROM campaign
                WHERE id = :campaign_id
            """)
            
            result = db.query(str(query), [campaign_id])
            
            if result and len(result) > 0:
                return Campaign(result[0])
            return None
            
        except Exception as e:
            logger.error(f"Error fetching campaign: {e}")
            return None
    
    @staticmethod
    async def update_campaign_status(campaign_id: str, status: str) -> bool:
        """Update campaign status"""
        try:
            query = text("""
                UPDATE campaign
                SET status = :status, updated_at = NOW()
                WHERE id = :campaign_id
            """)
            
            db.query(str(query), [status, campaign_id])
            return True
            
        except Exception as e:
            logger.error(f"Error updating campaign status: {e}")
            return False
    
    @staticmethod
    async def create_campaign_calls(campaign_id: str, leads: List[Dict[str, str]]) -> bool:
        """Create campaign calls from leads data"""
        try:
            with db.transaction() as client:
                # Insert all campaign calls
                for lead in leads:
                    insert_query = text("""
                        INSERT INTO campaign_call (
                            id, campaign_id, phone_number, country_code, status,
                            retry_count, max_retries, created_at, updated_at
                        ) VALUES (
                            gen_random_uuid(), :campaign_id, :phone_number, :country_code, 
                            'pending', 0, 3, NOW(), NOW()
                        )
                    """)
                    
                    client.execute(
                        insert_query,
                        {
                            'campaign_id': campaign_id,
                            'phone_number': lead['phone_number'],
                            'country_code': lead['country_code']
                        }
                    )
            
            return True
            
        except Exception as e:
            logger.error(f"Error creating campaign calls: {e}")
            return False
    
    @staticmethod
    async def get_campaign_calls_by_status(
        campaign_id: str, 
        status: str
    ) -> List[CampaignCall]:
        """Get campaign calls by status"""
        try:
            query = text("""
                SELECT
                    id, campaign_id, phone_number, country_code, status, call_sid,
                    duration, cost, error_message, retry_count, max_retries,
                    scheduled_at, started_at, completed_at, created_at, updated_at
                FROM campaign_call
                WHERE campaign_id = :campaign_id AND status = :status
                ORDER BY created_at ASC
            """)
            
            result = db.query(str(query), [campaign_id, status])
            return [CampaignCall(row) for row in result]
            
        except Exception as e:
            logger.error(f"Error fetching campaign calls: {e}")
            return []
    
    @staticmethod
    async def update_campaign_call_status(
        call_id: str,
        status: str,
        call_sid: Optional[str] = None,
        duration: Optional[int] = None,
        cost: Optional[float] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """Update campaign call status"""
        try:
            update_fields = ['status = :status', 'updated_at = NOW()']
            values = {'call_id': call_id, 'status': status}
            
            if call_sid:
                update_fields.append('call_sid = :call_sid')
                values['call_sid'] = call_sid
            
            if duration is not None:
                update_fields.append('duration = :duration')
                values['duration'] = duration
            
            if cost is not None:
                update_fields.append('cost = :cost')
                values['cost'] = cost
            
            if error_message:
                update_fields.append('error_message = :error_message')
                values['error_message'] = error_message
            
            if status == 'calling':
                update_fields.append('started_at = NOW()')
            elif status in ['completed', 'failed', 'busy', 'no-answer']:
                update_fields.append('completed_at = NOW()')
            
            query_str = f"""
                UPDATE campaign_call
                SET {', '.join(update_fields)}
                WHERE id = :call_id
            """
            
            db.query(query_str, [values.get(k) for k in ['call_id', 'status', 'call_sid', 'duration', 'cost', 'error_message'] if k in values])
            return True
            
        except Exception as e:
            logger.error(f"Error updating campaign call status: {e}")
            return False
    
    @staticmethod
    async def get_campaign_status(campaign_id: str) -> Optional[CampaignStatusResponse]:
        """Get campaign status with statistics"""
        try:
            campaign = await CampaignService.get_campaign_by_id(campaign_id)
            if not campaign:
                return None
            
            stats_query = text("""
                SELECT
                    status,
                    COUNT(*) as count
                FROM campaign_call
                WHERE campaign_id = :campaign_id
                GROUP BY status
            """)
            
            stats = db.query(str(stats_query), [campaign_id])
            
            status_counts = {
                'pending': 0,
                'calling': 0,
                'completed': 0,
                'failed': 0,
                'busy': 0,
                'no-answer': 0
            }
            
            for stat in stats:
                status_counts[stat['status']] = int(stat['count'])
            
            total_calls = sum(status_counts.values())
            completed_calls = status_counts['completed']
            success_rate = (completed_calls / total_calls * 100) if total_calls > 0 else 0
            
            # Calculate estimated completion time
            estimated_completion_time = None
            if status_counts['pending'] > 0:
                avg_call_duration = 30  # seconds
                estimated_seconds = status_counts['pending'] * avg_call_duration
                estimated_completion_time = datetime.now() + datetime.timedelta(seconds=estimated_seconds)
            
            return CampaignStatusResponse(
                campaign_id=campaign_id,
                status=campaign.status,
                total_leads=campaign.leads_count,
                completed_calls=completed_calls,
                failed_calls=status_counts['failed'] + status_counts['busy'] + status_counts['no-answer'],
                pending_calls=status_counts['pending'] + status_counts['calling'],
                success_rate=round(success_rate, 2),
                estimated_completion_time=estimated_completion_time
            )
            
        except Exception as e:
            logger.error(f"Error getting campaign status: {e}")
            return None
    
    @staticmethod
    async def get_next_pending_call(campaign_id: str) -> Optional[CampaignCall]:
        """Get next pending campaign call"""
        try:
            query = text("""
                SELECT
                    id, campaign_id, phone_number, country_code, status, call_sid,
                    duration, cost, error_message, retry_count, max_retries,
                    scheduled_at, started_at, completed_at, created_at, updated_at
                FROM campaign_call
                WHERE campaign_id = :campaign_id AND status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
            """)
            
            result = db.query(str(query), [campaign_id])
            
            if result and len(result) > 0:
                return CampaignCall(result[0])
            return None
            
        except Exception as e:
            logger.error(f"Error getting next pending call: {e}")
            return None
    
    @staticmethod
    async def is_campaign_complete(campaign_id: str) -> bool:
        """Check if campaign is complete (no pending calls)"""
        try:
            query = text("""
                SELECT COUNT(*) as pending_count
                FROM campaign_call
                WHERE campaign_id = :campaign_id AND status IN ('pending', 'calling')
            """)
            
            result = db.query(str(query), [campaign_id])
            pending_count = int(result[0]['pending_count']) if result else 0
            
            return pending_count == 0
            
        except Exception as e:
            logger.error(f"Error checking campaign completion: {e}")
            return False
    
    @staticmethod
    async def mark_campaign_completed(campaign_id: str) -> bool:
        """Mark campaign as completed"""
        try:
            is_complete = await CampaignService.is_campaign_complete(campaign_id)
            if is_complete:
                return await CampaignService.update_campaign_status(campaign_id, 'completed')
            return False
            
        except Exception as e:
            logger.error(f"Error marking campaign as completed: {e}")
            return False
