from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
import logging
from O365 import Account, FileSystemTokenBackend
from typing import Dict, Any, Optional, List
import pytz
from zoneinfo import ZoneInfo
import os
from .token_manager import TokenManager
import asyncio

logger = logging.getLogger(__name__)

@dataclass
class MicrosoftConfig:
    """Configuration for Microsoft Calendar integration"""
    client_id: str
    token_store_path: str = "~/.calendar_tokens"
    timezone: str = "Asia/Kolkata"  # Default to Indian timezone
    max_concurrent_operations: int = 3
    request_timeout: int = 30

class MicrosoftCalendar:
    def __init__(self, config: MicrosoftConfig):
        """Initialize Microsoft Calendar integration"""
        self.config = config
        self.token_manager = TokenManager(config.token_store_path)
        self.account = None
        self.calendar = None
        self._setup_lock = asyncio.Lock()
        
    async def setup(self):
        """Initialize and authenticate Microsoft Calendar connection"""
        async with self._setup_lock:
            try:
                if self.account and self.calendar:
                    return
                    
                token_path = os.path.expanduser(self.config.token_store_path)
                token_backend = FileSystemTokenBackend(token_path=token_path)
                
                self.account = Account(
                    credentials=(self.config.client_id,),
                    auth_flow_type='public',
                    token_backend=token_backend
                )
                
                self.account.connection.scopes = [
                    'offline_access',
                    'Calendars.ReadWrite',
                    'Calendars.Read',
                    'Calendars.Read.Shared',
                    'User.Read'
                ]

                if not self.account.is_authenticated:
                    result = await asyncio.to_thread(self.account.authenticate)
                    if not result:
                        raise RuntimeError("Microsoft Calendar authentication failed")
                    logger.info("Microsoft Calendar authentication successful")
                    
                self.calendar = self.account.schedule().get_default_calendar()
                
            except Exception as e:
                logger.error(f"Failed to setup Microsoft Calendar: {e}")
                raise

    def _convert_to_zoneinfo(self, dt: datetime, timezone: str) -> datetime:
        """Convert datetime to use ZoneInfo timezone"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(ZoneInfo(timezone))

    def _ensure_future_datetime(self, dt: datetime, buffer_minutes: int = 5) -> datetime:
        """Ensure datetime is in the future by at least buffer_minutes"""
        now = datetime.now(UTC)
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        
        if dt <= now:
            dt = now + timedelta(minutes=buffer_minutes)
        
        return dt

    async def create_event(
        self,
        title: str,
        description: str,
        start_time: datetime,
        end_time: datetime,
        attendees: List[str]
    ) -> Dict[str, Any]:
        """Create a new calendar event"""
        try:
            await self.setup()
            
            # Convert times to use ZoneInfo
            start_time = self._convert_to_zoneinfo(start_time, self.config.timezone)
            end_time = self._convert_to_zoneinfo(end_time, self.config.timezone)
            
            # Ensure times are in the future
            start_time = self._ensure_future_datetime(start_time)
            end_time = self._ensure_future_datetime(end_time)
            
            event = self.calendar.new_event()
            event.subject = title
            event.body = description
            event.start = start_time
            event.end = end_time
            event.attendees.add(attendees)
            
            if await asyncio.to_thread(event.save):
                logger.info(f"Successfully created event: {event.subject}")
                return {
                    'id': event.object_id,
                    'subject': event.subject,
                    'start': event.start.isoformat(),
                    'end': event.end.isoformat(),
                    'attendees': attendees
                }
            else:
                raise Exception("Failed to save event")
                
        except Exception as e:
            logger.error(f"Error creating event: {e}")
            raise

    async def get_event(self, event_id: str) -> Dict[str, Any]:
        """Get event details by ID"""
        try:
            await self.setup()
            
            event = await asyncio.to_thread(self.calendar.get_event, event_id)
            if event:
                return {
                    'id': event.object_id,
                    'subject': event.subject,
                    'body': event.body,
                    'start': event.start.isoformat(),
                    'end': event.end.isoformat(),
                    'attendees': [a.address for a in event.attendees]
                }
            else:
                raise Exception(f"Event not found: {event_id}")
                
        except Exception as e:
            logger.error(f"Error getting event: {e}")
            raise

    async def update_event(
        self,
        event_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        attendees: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Update an existing calendar event"""
        try:
            await self.setup()
            
            event = await asyncio.to_thread(self.calendar.get_event, event_id)
            if not event:
                raise Exception(f"Event not found: {event_id}")
            
            if title:
                event.subject = title
            if description:
                event.body = description
            if start_time:
                event.start = self._convert_to_zoneinfo(start_time, self.config.timezone)
            if end_time:
                event.end = self._convert_to_zoneinfo(end_time, self.config.timezone)
            if attendees:
                event.attendees.clear()
                event.attendees.add(attendees)
            
            if await asyncio.to_thread(event.save):
                logger.info(f"Successfully updated event: {event.subject}")
                return {
                    'id': event.object_id,
                    'subject': event.subject,
                    'start': event.start.isoformat(),
                    'end': event.end.isoformat(),
                    'attendees': [a.address for a in event.attendees]
                }
            else:
                raise Exception("Failed to update event")
                
        except Exception as e:
            logger.error(f"Error updating event: {e}")
            raise

    async def delete_event(self, event_id: str) -> bool:
        """Delete a calendar event"""
        try:
            await self.setup()
            
            event = await asyncio.to_thread(self.calendar.get_event, event_id)
            if not event:
                raise Exception(f"Event not found: {event_id}")
            
            if await asyncio.to_thread(event.delete):
                logger.info(f"Successfully deleted event: {event_id}")
                return True
            else:
                raise Exception("Failed to delete event")
                
        except Exception as e:
            logger.error(f"Error deleting event: {e}")
            raise

    async def get_calendar_events(
        self,
        start_time: datetime,
        end_time: datetime
    ) -> List[Dict[str, Any]]:
        """Get all calendar events between start_time and end_time"""
        try:
            await self.setup()
            
            # Convert times to use ZoneInfo
            start_time = self._convert_to_zoneinfo(start_time, self.config.timezone)
            end_time = self._convert_to_zoneinfo(end_time, self.config.timezone)
            
            q = self.calendar.new_query('start').greater_equal(start_time)
            q.chain('and').on_attribute('end').less_equal(end_time)
            
            events = []
            async for event in asyncio.to_thread(self.calendar.get_events, query=q, include_recurring=True):
                events.append({
                    'id': event.object_id,
                    'subject': event.subject,
                    'start': event.start.isoformat(),
                    'end': event.end.isoformat(),
                    'attendees': [a.address for a in event.attendees]
                })
            
            return events
                
        except Exception as e:
            logger.error(f"Error getting calendar events: {e}")
            raise

    async def cleanup(self):
        """Cleanup resources"""
        try:
            self.account = None
            self.calendar = None
            logger.info("Cleaned up Microsoft Calendar resources")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")