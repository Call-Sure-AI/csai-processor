from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
import logging
from typing import Dict, Any, Optional, List
import asyncio
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pytz
from zoneinfo import ZoneInfo
import os
from .token_manager import TokenManager
from .types import (
    CalendarConfig,
    AppointmentData,
    EventOptions,
    AvailabilityOptions,
)

logger = logging.getLogger(__name__)

class AsyncGoogleCalendar:
    def __init__(self, config: CalendarConfig):
        """Initialize Google Calendar integration"""
        self.config = config
        self.token_manager = TokenManager(config.token_store_path)
        self.service = None
        self._setup_lock = asyncio.Lock()
        
    async def setup(self):
        """Initialize and authenticate Google Calendar connection"""
        async with self._setup_lock:
            try:
                if self.service:
                    return
                    
                if not self.config.service_account_json:
                    logger.error("Missing Service Account JSON file path.")
                    raise ValueError("Service Account JSON path is required")
                if not self.config.calendar_id:
                    logger.error("Missing Google Calendar ID.")
                    raise ValueError("Calendar ID must be explicitly set for service account")

                SCOPES = ['https://www.googleapis.com/auth/calendar']
                
                credentials = await asyncio.to_thread(
                    service_account.Credentials.from_service_account_file,
                    self.config.service_account_json,
                    scopes=SCOPES
                )

                self.service = await asyncio.to_thread(
                    build,
                    'calendar',
                    'v3',
                    credentials=credentials
                )

                # Validate that the Calendar ID is accessible
                try:
                    calendar_check = self.service.calendars().get(calendarId=self.config.calendar_id)
                    await asyncio.to_thread(calendar_check.execute)
                    logger.info("Successfully connected to Google Calendar via Service Account")
                except HttpError as e:
                    logger.error(f"Failed to access calendar. Please ensure that your service account has permissions. Error: {e}")
                    raise ValueError(f"Failed to access Google Calendar: {e}")

            except Exception as e:
                logger.error(f"Failed to setup Google Calendar: {e}", exc_info=True)
                raise

    async def create_event(
        self,
        appointment_data: AppointmentData,
        options: EventOptions
    ) -> Dict[str, Any]:
        """Create a new calendar event"""
        try:
            await self.setup()
            
            description = appointment_data.description
            if options.add_attendees_to_description and appointment_data.attendees:
                description = (
                    f"{description}\n\n"
                    f"Attendees:\n"
                    f"{', '.join(appointment_data.attendees)}"
                )

            event = {
                'summary': appointment_data.title,
                'description': description,
                'start': {
                    'dateTime': appointment_data.start_time.isoformat(),
                    'timeZone': appointment_data.timezone,
                },
                'end': {
                    'dateTime': appointment_data.end_time.isoformat(),
                    'timeZone': appointment_data.timezone,
                }
            }

            if options.include_attendees:
                event['attendees'] = [{'email': attendee} for attendee in appointment_data.attendees]

            if options.reminders_enabled:
                if options.custom_reminders:
                    event['reminders'] = {
                        'useDefault': False,
                        'overrides': options.custom_reminders
                    }
                else:
                    event['reminders'] = {'useDefault': True}

            create_kwargs = {
                'calendarId': self.config.calendar_id,
                'body': event
            }
            
            if options.send_updates:
                create_kwargs['sendUpdates'] = options.send_updates

            # Separate request building from execution
            event_request = self.service.events().insert(**create_kwargs)
            created_event = await asyncio.to_thread(event_request.execute)
            
            logger.info(f"Successfully created event: {created_event.get('id')}")
            return created_event
                
        except Exception as e:
            logger.error(f"Error creating event: {e}")
            raise

    async def check_availability(
        self,
        start_time: datetime,
        end_time: datetime
    ) -> bool:
        """Check if a time slot is available"""
        try:
            await self.setup()
            
            # Separate request building from execution
            events_request = self.service.events().list(
                calendarId=self.config.calendar_id,
                timeMin=start_time.isoformat(),
                timeMax=end_time.isoformat(),
                singleEvents=True
            )
            events_result = await asyncio.to_thread(events_request.execute)
            
            return len(events_result.get('items', [])) == 0
                
        except Exception as e:
            logger.error(f"Error checking availability: {e}")
            raise

    async def get_availability(
        self,
        start_date: datetime,
        end_date: datetime,
        options: AvailabilityOptions,
        duration_minutes: int
    ) -> List[Dict[str, Any]]:
        """Get available time slots between start_date and end_date."""
        try:
            await self.setup()

            # Separate request building from execution
            events_request = self.service.events().list(
                calendarId=self.config.calendar_id,
                timeMin=start_date.isoformat(),
                timeMax=end_date.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            )
            events_result = await asyncio.to_thread(events_request.execute)

            # Process events more efficiently using list comprehension
            busy_periods = [
                {
                    'start': datetime.fromisoformat(event['start']['dateTime'].replace('Z', '+00:00')),
                    'end': datetime.fromisoformat(event['end']['dateTime'].replace('Z', '+00:00'))
                }
                for event in events_result.get('items', [])
                if 'dateTime' in event['start'] and 'dateTime' in event['end']
            ]

            if options.buffer_minutes > 0:
                busy_periods = [
                    {
                        'start': period['start'] - timedelta(minutes=options.buffer_minutes),
                        'end': period['end'] + timedelta(minutes=options.buffer_minutes)
                    }
                    for period in busy_periods
                ]

            available_slots = []
            current_time = start_date

            excluded_dates = {d.date() if isinstance(d, datetime) else d for d in options.excluded_dates}

            while current_time < end_date:
                # Skip excluded dates efficiently
                if current_time.date() in excluded_dates:
                    current_time += timedelta(days=1)
                    current_time = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
                    continue

                # Get working hours for the day
                day_name = current_time.strftime('%A').lower()
                working_hours = options.working_hours.get(day_name)
                
                if not working_hours:
                    current_time += timedelta(days=1)
                    continue

                # Check custom availability if it exists
                if options.custom_availability:
                    custom_hours = next(
                        (hours for date, hours in options.custom_availability.items() 
                         if current_time.date() == date.date()),
                        None
                    )
                    if custom_hours:
                        working_hours = custom_hours

                # Convert working hours to datetime
                start_time = datetime.combine(
                    current_time.date(),
                    datetime.strptime(working_hours['start'], '%H:%M').time()
                )
                end_time = datetime.combine(
                    current_time.date(),
                    datetime.strptime(working_hours['end'], '%H:%M').time()
                )

                # Generate slots
                while start_time + timedelta(minutes=duration_minutes) <= end_time:
                    slot_end = start_time + timedelta(minutes=duration_minutes)

                    # Check if slot overlaps with busy periods
                    if not any(start_time < busy['end'] and slot_end > busy['start'] 
                             for busy in busy_periods):
                        available_slots.append({
                            'start': start_time.isoformat(),
                            'end': slot_end.isoformat(),
                            'duration_minutes': duration_minutes,
                            'timezone': options.timezone
                        })

                    start_time += timedelta(minutes=duration_minutes)

                current_time += timedelta(days=1)
                current_time = current_time.replace(hour=0, minute=0, second=0, microsecond=0)

            return available_slots

        except Exception as e:
            logger.error(f"Error getting availability: {e}", exc_info=True)
            raise

    async def delete_event(self, event_id: str) -> bool:
        """Delete a calendar event"""
        try:
            await self.setup()
            
            # Separate request building from execution
            delete_request = self.service.events().delete(
                calendarId=self.config.calendar_id,
                eventId=event_id,
                sendUpdates='all'
            )
            await asyncio.to_thread(delete_request.execute)
            
            logger.info(f"Successfully deleted event: {event_id}")
            return True
                
        except Exception as e:
            logger.error(f"Error deleting event: {e}")
            raise

    async def cleanup(self):
        """Cleanup resources"""
        try:
            if self.service:
                self.service = None  # Just unset the service
            logger.info("Cleaned up Google Calendar resources")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            raise
