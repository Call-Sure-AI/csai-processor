import asyncio
import httpx
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from twilio.rest import Client
from sqlalchemy.orm import Session
from database.models import OutboundCall
from config.settings import settings

logger = logging.getLogger(__name__)

class OutboundCallService:
    """Service for making outbound calls for campaigns"""
    
    def __init__(self):
        self.twilio_client = Client(
            settings.twilio_account_sid,
            settings.twilio_auth_token
        )
        self.from_number = settings.twilio_phone_number
        self.base_url = settings.base_url or "https://processor.callsure.ai"
        self.api_base = "https://beta.callsure.ai/api"
        self.auth_token = settings.callsure_api_token
        self.max_attempts = 3
        self.retry_interval_minutes = 60
    
    def _get_headers(self) -> Dict[str, str]:
        """Get API headers"""
        return {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json"
        }
    
    async def get_campaign(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        """Fetch campaign from API"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_base}/campaigns/{campaign_id}",
                    headers=self._get_headers(),
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(f"Failed to get campaign: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error fetching campaign: {str(e)}")
            return None
    
    async def download_leads_csv(self, csv_url: str) -> List[Dict[str, Any]]:
        """Download and parse leads CSV from S3"""
        try:
            import pandas as pd
            import io
            
            async with httpx.AsyncClient() as client:
                response = await client.get(csv_url, timeout=30.0)
                
                if response.status_code == 200:
                    csv_content = response.content.decode('utf-8')
                    df = pd.read_csv(io.StringIO(csv_content))
                    leads = df.to_dict('records')
                    logger.info(f"Downloaded {len(leads)} leads from CSV")
                    return leads
                else:
                    logger.error(f"Failed to download CSV: {response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error downloading CSV: {str(e)}")
            return []
    
    async def make_call(
        self,
        to_number: str,
        campaign_id: str,
        lead_data: Dict[str, Any],
        agent_id: str,
        company_id: str,
        db: Session
    ) -> Dict[str, Any]:
        """Make an outbound call to a lead"""
        try:
            logger.info(f"Making outbound call to {to_number}")
            
            # Check existing attempts
            existing_calls = db.query(OutboundCall).filter(
                OutboundCall.campaign_id == campaign_id,
                OutboundCall.to_number == to_number
            ).all()
            
            attempt_number = len(existing_calls) + 1
            
            if attempt_number > self.max_attempts:
                logger.warning(f"Max attempts reached for {to_number}")
                return {"success": False, "error": "Max attempts reached"}
            
            # Format phone number
            if not to_number.startswith('+'):
                to_number = f"+{to_number}".replace(' ', '').replace('-', '')
            
            # URL encode customer name
            import urllib.parse
            first_name = lead_data.get('first_name', lead_data.get('Name', ''))
            last_name = lead_data.get('last_name', '')
            customer_name = urllib.parse.quote(f"{first_name} {last_name}".strip())
            
            # Create TwiML URL
            twiml_url = (
                f"{self.base_url}/api/v1/twilio-elevenlabs/outbound-connect?"
                f"campaign_id={campaign_id}&"
                f"agent_id={agent_id}&"
                f"company_id={company_id}&"
                f"customer_name={customer_name}&"
                f"attempt={attempt_number}"
            )
            
            # Make Twilio call
            call = self.twilio_client.calls.create(
                to=to_number,
                from_=self.from_number,
                url=twiml_url,
                status_callback=f"{self.base_url}/api/v1/outbound/status",
                status_callback_event=['initiated', 'ringing', 'answered', 'completed'],
                status_callback_method='POST',
                method='POST',
                timeout=30,
                record=True
            )
            
            # Save to local DB for tracking
            outbound_call = OutboundCall(
                campaign_id=campaign_id,
                call_sid=call.sid,
                to_number=to_number,
                from_number=self.from_number,
                status='initiated',
                attempt_number=attempt_number,
                lead_data=lead_data,
                created_at=datetime.utcnow()
            )
            db.add(outbound_call)
            db.commit()
            
            logger.info(f"✓ Call initiated: {call.sid}")
            
            return {
                "success": True,
                "call_sid": call.sid,
                "attempt_number": attempt_number
            }
            
        except Exception as e:
            logger.error(f"Error making call: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return {"success": False, "error": str(e)}
    
    async def process_campaign(
        self,
        campaign_id: str,
        db: Session
    ) -> Dict[str, Any]:
        """Process all leads in a campaign"""
        try:
            logger.info(f"Processing campaign: {campaign_id}")
            
            # Get campaign from API
            campaign = await self.get_campaign(campaign_id)
            
            if not campaign:
                return {"success": False, "error": "Campaign not found"}
            
            if campaign.get("status") != "active":
                return {"success": False, "error": "Campaign not active"}
            
            # Download leads CSV
            csv_url = campaign.get("leads_file_url")
            if not csv_url:
                return {"success": False, "error": "No leads CSV found"}
            
            leads = await self.download_leads_csv(csv_url)
            
            if not leads:
                return {"success": False, "error": "No leads found"}
            
            total_calls = 0
            successful_calls = 0
            failed_calls = 0
            
            agent_id = campaign.get("agent_id")
            company_id = campaign.get("company_id")
            
            # Get data mapping
            data_mapping = campaign.get("data_mapping", [])
            phone_mapping = next((m for m in data_mapping if m["mapped_to"] == "phone"), None)
            phone_column = phone_mapping["csv_column"] if phone_mapping else "Phone"
            
            for lead in leads:
                phone = lead.get(phone_column, lead.get('phone', ''))
                
                if not phone:
                    logger.warning(f"No phone for lead: {lead}")
                    continue
                
                # Make call
                result = await self.make_call(
                    to_number=str(phone),
                    campaign_id=campaign_id,
                    lead_data=lead,
                    agent_id=agent_id,
                    company_id=company_id,
                    db=db
                )
                
                total_calls += 1
                
                if result.get("success"):
                    successful_calls += 1
                else:
                    failed_calls += 1
                
                # Delay between calls
                await asyncio.sleep(2)
            
            logger.info(f"Campaign {campaign_id}: {successful_calls}/{total_calls} successful")
            
            return {
                "success": True,
                "campaign_id": campaign_id,
                "total_calls": total_calls,
                "successful_calls": successful_calls,
                "failed_calls": failed_calls
            }
            
        except Exception as e:
            logger.error(f"Error processing campaign: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return {"success": False, "error": str(e)}
    
    async def retry_failed_calls(
        self,
        campaign_id: str,
        db: Session
    ) -> Dict[str, Any]:
        """Retry failed or no-answer calls"""
        try:
            one_hour_ago = datetime.utcnow() - timedelta(hours=1)
            
            failed_calls = db.query(OutboundCall).filter(
                OutboundCall.campaign_id == campaign_id,
                OutboundCall.status.in_(['no-answer', 'busy', 'failed']),
                OutboundCall.attempt_number < self.max_attempts,
                OutboundCall.created_at >= one_hour_ago
            ).all()
            
            retried = 0
            campaign = await self.get_campaign(campaign_id)
            
            if not campaign:
                return {"success": False, "error": "Campaign not found"}
            
            for call in failed_calls:
                result = await self.make_call(
                    to_number=call.to_number,
                    campaign_id=campaign_id,
                    lead_data=call.lead_data,
                    agent_id=campaign.get("agent_id"),
                    company_id=campaign.get("company_id"),
                    db=db
                )
                
                if result.get("success"):
                    retried += 1
                
                await asyncio.sleep(2)
            
            return {"success": True, "retried_calls": retried}
            
        except Exception as e:
            logger.error(f"Error retrying calls: {str(e)}")
            return {"success": False, "error": str(e)}

# Global instance
outbound_call_service = OutboundCallService()
