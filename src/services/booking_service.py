# src\services\booking_service.py
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
    async def get_bookings_for_campaign(
        self,
        campaign_id: str,
        start_date: str,
        end_date: str
    ) -> List[Dict]:
        """Fetch existing bookings for a campaign within date range"""
        
        try:
            params = {
                "campaign_id": campaign_id,
                "start_date": start_date,
                "end_date": end_date
            }
            
            logger.info(f"Fetching bookings for campaign {campaign_id} from {start_date} to {end_date}")
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    self.base_url,
                    params=params,
                    headers=self._get_headers()
                )
                
                if response.status_code == 200:
                    bookings = response.json()
                    logger.info(f"Found {len(bookings)} bookings")
                    return bookings
                else:
                    logger.error(f"Failed to fetch bookings: {response.status_code} - {response.text}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error fetching bookings: {str(e)}")
            return []
    
    async def check_slot_availability(
        self,
        campaign_id: str,
        slot_start: str,
        slot_end: str,
        customer_phone: str
    ) -> Dict:
        """
        Check if a slot is available
        Rules:
        - Max 2 bookings per slot
        - No duplicate booking for same phone number
        """
        
        try:
            # Parse slot datetime
            slot_dt = datetime.fromisoformat(slot_start.replace('Z', ''))
            
            # Fetch bookings for the day
            start_date = slot_dt.strftime("%Y-%m-%d")
            end_date = slot_dt.strftime("%Y-%m-%d")
            
            bookings = await self.get_bookings_for_campaign(
                campaign_id=campaign_id,
                start_date=start_date,
                end_date=end_date
            )
            
            # Filter bookings for this specific slot (excluding cancelled)
            slot_bookings = [
                b for b in bookings
                if b.get('slot_start') == slot_start and b.get('status') != 'cancelled'
            ]
            
            logger.info(f"Slot {slot_start}: {len(slot_bookings)}/2 bookings")
            
            # Check if customer already has a booking in this slot
            customer_booking = next(
                (b for b in slot_bookings if b.get('customer_phone') == customer_phone),
                None
            )
            
            if customer_booking:
                return {
                    'available': False,
                    'reason': f"You already have a booking for this time slot",
                    'existing_bookings': len(slot_bookings),
                    'customer_already_booked': True,
                    'existing_booking': customer_booking
                }
            
            # Check if slot is full (max 2 bookings)
            if len(slot_bookings) >= 2:
                return {
                    'available': False,
                    'reason': "This time slot is fully booked",
                    'existing_bookings': len(slot_bookings),
                    'customer_already_booked': False
                }
            
            # Slot is available
            return {
                'available': True,
                'reason': f"Slot available ({len(slot_bookings)}/2 bookings)",
                'existing_bookings': len(slot_bookings),
                'customer_already_booked': False
            }
            
        except Exception as e:
            logger.error(f"Error checking slot availability: {str(e)}")
            return {
                'available': False,
                'reason': "Unable to check availability",
                'existing_bookings': 0,
                'customer_already_booked': False
            }
    
    def spell_out_email(self, email: str) -> str:
        """Spell out email for verification"""
        
        parts = email.lower().split('@')
        if len(parts) != 2:
            return email
        
        username, domain = parts
        
        # Spell out username
        username_spelled = ", ".join(list(username))
        
        # Spell out domain
        domain_parts = domain.split('.')
        domain_spelled = " dot ".join(
            ", ".join(list(part)) for part in domain_parts
        )
        
        return f"{username_spelled}, at, {domain_spelled}"

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
