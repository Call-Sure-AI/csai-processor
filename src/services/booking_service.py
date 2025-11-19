import httpx
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from config.settings import settings

logger = logging.getLogger(__name__)

class BookingService:
    """Service for managing bookings via Callsure API"""
    
    def __init__(self):
        self.base_url = "https://beta.callsure.ai/api/bookings"
        self.auth_token = settings.callsure_api_token
        self.timeout = 30.0
    
    def _get_headers(self) -> Dict[str, str]:
        """Get authorization headers"""
        return {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json"
        }
    
    async def create_booking(
        self,
        campaign_id: str,
        customer_name: str,
        customer_phone: str,
        slot_start: str,
        slot_end: str,
        customer_email: Optional[str] = None,
        notes: Optional[str] = None,
        status: str = "pending"
    ) -> Dict[str, Any]:
        """Create a new booking"""
        try:
            payload = {
                "campaign_id": campaign_id,
                "customer": customer_name,
                "slot_start": slot_start,
                "slot_end": slot_end,
                "status": status,
                "customer_phone": customer_phone,
                "customer_email": customer_email or f"{customer_phone.replace('+', '')}@temp.com",
                "notes": notes or "Booking created via voice agent"
            }
            
            logger.info(f"Creating booking for {customer_name} at {slot_start}")
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.base_url,
                    headers=self._get_headers(),
                    json=payload,
                    timeout=self.timeout
                )
                
                if response.status_code in [200, 201]:
                    result = response.json()
                    booking_id = result.get("id")
                    logger.info(f"✓ Booking created: {booking_id}")
                    return {
                        "success": True,
                        "booking_id": booking_id,
                        "booking": result
                    }
                else:
                    logger.error(f"Booking creation failed: {response.status_code} - {response.text}")
                    return {
                        "success": False,
                        "error": f"Failed to create booking: {response.text}"
                    }
                    
        except Exception as e:
            logger.error(f"Error creating booking: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

# Global instance
booking_service = BookingService()
