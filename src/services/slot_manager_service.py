# src/services/slot_manager_service.py
import os
import aiohttp
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger(__name__)

FASTAPI_BASE_URL = os.getenv("FASTAPI_BASE_URL", "https://beta.callsure.ai")


class SlotManagerService:
    """
    Fetches available slots from csai-fastapi backend.
    Falls back to suggesting general time ranges if DB slots unavailable.
    """
    
    def __init__(self):
        self.base_url = FASTAPI_BASE_URL
    
    async def get_available_slots(
        self,
        campaign_id: str,
        start_date: datetime,
        end_date: datetime,
        count: int = 5,
        auth_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fetch available slots from backend API.
        
        Returns:
            {
                "has_slots": True/False,
                "slots": [...],
                "count": int,
                "source": "database" | "fallback"
            }
        """
        
        try:
            url = f"{self.base_url}/api/bookings/campaigns/{campaign_id}/available-slots"
            params = {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "count": count
            }
            
            headers = {}
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        slots = data.get("slots", [])
                        
                        if slots:
                            logger.info(f"✅ Fetched {len(slots)} slots from database for campaign {campaign_id}")
                            return {
                                "has_slots": True,
                                "slots": slots,
                                "count": len(slots),
                                "source": "database"
                            }
                    else:
                        logger.warning(f"⚠️ API returned status {response.status} for campaign {campaign_id}")
        
        except Exception as e:
            logger.error(f"❌ Error fetching slots from API: {e}")
        
        # Fallback to suggesting general time ranges
        logger.info(f"🔄 Using fallback time suggestions for campaign {campaign_id}")
        return self._get_fallback_suggestions(start_date)
    
    def _get_fallback_suggestions(self, start_date: datetime) -> Dict[str, Any]:
        """
        Fallback: Suggest general time ranges when DB slots unavailable.
        """
        
        # Suggest next 3 business days, morning/afternoon
        suggestions = []
        current_date = start_date.date()
        
        for i in range(3):
            # Skip weekends
            while current_date.weekday() >= 5:  # Saturday=5, Sunday=6
                current_date += timedelta(days=1)
            
            # Morning slot (10 AM)
            morning = datetime.combine(current_date, datetime.strptime("10:00", "%H:%M").time())
            suggestions.append({
                "start": morning.isoformat(),
                "end": (morning + timedelta(minutes=30)).isoformat(),
                "label": f"{current_date.strftime('%A')} morning at 10 AM"
            })
            
            # Afternoon slot (2 PM)
            afternoon = datetime.combine(current_date, datetime.strptime("14:00", "%H:%M").time())
            suggestions.append({
                "start": afternoon.isoformat(),
                "end": (afternoon + timedelta(minutes=30)).isoformat(),
                "label": f"{current_date.strftime('%A')} afternoon at 2 PM"
            })
            
            current_date += timedelta(days=1)
        
        return {
            "has_slots": False,
            "slots": suggestions[:5],  # Return top 5
            "count": len(suggestions[:5]),
            "source": "fallback"
        }
    
    async def check_slot_capacity(
        self,
        campaign_id: str,
        slot_start: datetime,
        slot_end: datetime,
        auth_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Check if a specific slot has available capacity.
        """
        
        try:
            url = f"{self.base_url}/api/bookings/campaigns/{campaign_id}/slot-capacity"
            params = {
                "slot_start": slot_start.isoformat(),
                "slot_end": slot_end.isoformat()
            }
            
            headers = {}
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=5) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.warning(f"⚠️ Slot capacity check failed: {response.status}")
                        return {"available": True, "current_bookings": 0, "max_capacity": 1}
        
        except Exception as e:
            logger.error(f"❌ Error checking slot capacity: {e}")
            # Default to available with capacity 1
            return {"available": True, "current_bookings": 0, "max_capacity": 1, "available_capacity": 1}
    
    def format_slots_for_prompt(self, slots_data: Dict[str, Any]) -> str:
        """
        Format slots for inclusion in LLM prompt.
        """
        
        # Check if we have any slots (database OR fallback)
        slots = slots_data.get("slots", [])
        if not slots:
            return "No specific slots available. Suggest general time ranges (morning: 9-11 AM, afternoon: 2-4 PM)."
        
        source = slots_data.get("source", "unknown")
        
        if source == "database":
            # Format database slots with capacity info
            formatted = ["Available slots from your calendar:"]
            for i, slot in enumerate(slots[:5], 1):
                try:
                    start = datetime.fromisoformat(slot["start"].replace('Z', '+00:00'))
                    capacity_info = ""
                    if slot.get("max_capacity", 1) > 1:
                        available = slot.get("available_capacity", 1)
                        total = slot.get("max_capacity", 1)
                        capacity_info = f" ({available}/{total} spots)"
                    formatted.append(f"{i}. {start.strftime('%A, %B %d at %I:%M %p')}{capacity_info}")
                except Exception as e:
                    logger.error(f"Error formatting slot: {e}")
                    continue
            
            if len(formatted) == 1:  # Only header, no valid slots
                return "No specific slots available. Suggest general time ranges (morning: 9-11 AM, afternoon: 2-4 PM)."
            
            return "\n".join(formatted)
        
        else:
            # Format fallback suggestions (FIX: Actually format them!)
            formatted = ["Suggested times:"]
            for i, slot in enumerate(slots[:5], 1):
                label = slot.get('label')
                if label:
                    formatted.append(f"{i}. {label}")
                else:
                    # Fallback formatting if label missing
                    try:
                        start = datetime.fromisoformat(slot["start"].replace('Z', '+00:00'))
                        formatted.append(f"{i}. {start.strftime('%A, %B %d at %I:%M %p')}")
                    except:
                        continue
            
            if len(formatted) == 1:  # Only header, no valid slots
                return "No specific slots available. Suggest general time ranges (morning: 9-11 AM, afternoon: 2-4 PM)."
            
            return "\n".join(formatted)