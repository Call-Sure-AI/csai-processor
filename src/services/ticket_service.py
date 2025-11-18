import httpx
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from config.settings import settings

logger = logging.getLogger(__name__)

class TicketService:
    """Service for managing tickets via Callsure API"""
    
    def __init__(self):
        self.base_url = "https://beta.callsure.ai/api/tickets"
        self.auth_token = settings.callsure_api_token
        self.timeout = 30.0
    
    def _get_headers(self) -> Dict[str, str]:
        """Get authorization headers"""
        return {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json"
        }
    
    async def create_ticket(
        self,
        company_id: str,
        title: str,
        description: str,
        customer_id: Optional[str] = None,
        customer_phone: Optional[str] = None,
        customer_name: Optional[str] = None,
        priority: str = "medium",
        tags: Optional[List[str]] = None,
        meta_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Create a new ticket"""
        try:
            # Generate ticket ID
            ticket_id = f"TKT-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            payload = {
                "id": ticket_id,
                "company_id": company_id,
                "customer_id": customer_id or f"CUST-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "title": title,
                "description": description,
                "priority": priority,
                "tags": tags or ["support", "voice-call"],
                "meta_data": meta_data or {"source": "voice_agent"}
            }
            
            # Add customer details if provided
            if customer_phone:
                payload["customer_phone"] = customer_phone
            if customer_name:
                payload["customer_name"] = customer_name
            
            logger.info(f"Creating ticket: {ticket_id}")
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/companies/{company_id}/create",
                    headers=self._get_headers(),
                    json=payload,
                    timeout=self.timeout
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"Ticket created: {ticket_id}")
                    return {
                        "success": True,
                        "ticket_id": ticket_id,
                        "message": "Ticket created successfully",
                        "ticket": result.get("ticket")
                    }
                else:
                    logger.error(f"Ticket creation failed: {response.status_code} - {response.text}")
                    return {
                        "success": False,
                        "error": f"Failed to create ticket: {response.text}"
                    }
                    
        except Exception as e:
            logger.error(f"Error creating ticket: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def update_ticket(
        self,
        company_id: str,
        ticket_id: str,
        status: Optional[str] = None,
        assigned_to: Optional[str] = None,
        priority: Optional[str] = None,
        note: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update ticket status"""
        try:
            payload = {}
            if status:
                payload["status"] = status
            if assigned_to:
                payload["assigned_to"] = assigned_to
            if priority:
                payload["priority"] = priority
            if note:
                payload["note"] = note
            
            logger.info(f"Updating ticket: {ticket_id}")
            
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{self.base_url}/companies/{company_id}/{ticket_id}",
                    headers=self._get_headers(),
                    json=payload,
                    timeout=self.timeout
                )
                
                if response.status_code == 200:
                    logger.info(f"Ticket updated: {ticket_id}")
                    return {
                        "success": True,
                        "message": "Ticket updated successfully"
                    }
                else:
                    logger.error(f"Ticket update failed: {response.status_code}")
                    return {
                        "success": False,
                        "error": f"Failed to update ticket: {response.text}"
                    }
                    
        except Exception as e:
            logger.error(f"Error updating ticket: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_ticket(
        self,
        company_id: str,
        ticket_id: str
    ) -> Dict[str, Any]:
        """Get ticket details"""
        try:
            logger.info(f"Fetching ticket: {ticket_id}")
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/companies/{company_id}/{ticket_id}",
                    headers=self._get_headers(),
                    timeout=self.timeout
                )
                
                if response.status_code == 200:
                    ticket = response.json()
                    logger.info(f"✓ Ticket fetched: {ticket_id}")
                    return {
                        "success": True,
                        "ticket": ticket
                    }
                else:
                    logger.error(f"Ticket fetch failed: {response.status_code}")
                    return {
                        "success": False,
                        "error": f"Failed to fetch ticket: {response.text}"
                    }
                    
        except Exception as e:
            logger.error(f"Error fetching ticket: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def analyze_conversation(
        self,
        conversation_id: str,
        messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """Analyze conversation to determine if ticket should be created"""
        try:
            logger.info(f"Analyzing conversation: {conversation_id}")
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/analyze-conversation/{conversation_id}",
                    headers=self._get_headers(),
                    json=messages,
                    timeout=self.timeout
                )
                
                if response.status_code == 200:
                    analysis = response.json()
                    logger.info(f"Conversation analyzed: should_create={analysis.get('should_create_ticket')}")
                    return {
                        "success": True,
                        "analysis": analysis
                    }
                else:
                    logger.error(f"Analysis failed: {response.status_code}")
                    return {
                        "success": False,
                        "error": f"Failed to analyze conversation: {response.text}"
                    }
                    
        except Exception as e:
            logger.error(f"Error analyzing conversation: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

# Global instance
ticket_service = TicketService()
