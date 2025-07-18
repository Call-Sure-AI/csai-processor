from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union, Any
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError
from O365 import Account, FileSystemTokenBackend
import requests
from requests.exceptions import RequestException, HTTPError
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log
from google.oauth2 import service_account

from .token_manager import TokenManager
from .types import (
    CalendarConfig,
    CalendarType,
    AppointmentData,
    EventOptions,
    AvailabilityOptions,
)

logger = logging.getLogger(__name__)

class RateLimiter:
    """Rate limit implementation"""
    def __init__(self, calls_per_minute: int):
        self.calls_per_minute = calls_per_minute
        self.calls = []
        self.lock = threading.Lock()

    def acquire(self) -> bool:
        """Try to acquire a rate limit token"""
        with self.lock:
            now = datetime.now()
            self.calls = [t for t in self.calls if now - t < timedelta(minutes=1)]
            
            if len(self.calls) < self.calls_per_minute:
                self.calls.append(now)
                return True
            return False

class CalendarIntegration:
    """Calendar integration with standard exception handling"""
    
    def __init__(self, config: CalendarConfig):
        self.config = config
        self.google_service = None
        self.microsoft_account = None
        self.calendly_token = None
        
        self.token_manager = TokenManager(config.token_store_path)
        self.rate_limiter = RateLimiter(config.rate_limit_per_minute)
        self.executor = ThreadPoolExecutor(max_workers=config.max_concurrent_operations)
        
        # Initialize enabled services
        if config.service_account_json:
            self.setup_google_calendar()
        if config.microsoft_client_id and config.microsoft_client_secret:
            self.setup_microsoft_calendar()
        if config.calendly_token:
            self.setup_calendly()

    def _check_rate_limit(self):
        """Check rate limit before making API calls"""
        if not self.rate_limiter.acquire():
            raise RuntimeError("Rate limit exceeded")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        before_sleep=before_sleep_log(logger, logging.INFO)
    )
    def setup_google_calendar(self):
        """Initialize Google Calendar API connection using a Service Account"""
        try:
            if not self.config.service_account_json:
                raise ValueError("Service Account JSON path is required")
            if not self.config.calendar_id:
                raise ValueError("Calendar ID must be explicitly set for service account")

            SCOPES = ['https://www.googleapis.com/auth/calendar']
            credentials = service_account.Credentials.from_service_account_file(
                self.config.service_account_json, 
                scopes=SCOPES
            )

            # Build and test the service
            self.google_service = build('calendar', 'v3', credentials=credentials)
            
            # Test connection with the specific calendar ID
            try:
                self.google_service.calendars().get(
                    calendarId=self.config.calendar_id
                ).execute()
                logger.info("Successfully connected to Google Calendar via Service Account")
            except HttpError as e:
                raise ValueError(f"Failed to access calendar. Make sure you've shared it with the service account: {e}")

        except Exception as e:
            logger.error(f"Failed to setup Google Calendar: {e}", exc_info=True)
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        before_sleep=before_sleep_log(logger, logging.INFO)
    )
    def setup_microsoft_calendar(self):
        """Initialize Microsoft Calendar API connection"""
        try:
            # Set up token backend separately
            token_backend = FileSystemTokenBackend(
                token_path=self.config.token_store_path
            )
            
            # Create and configure the Account
            self.microsoft_account = Account(
                credentials=(self.config.microsoft_client_id, self.config.microsoft_client_secret),
                token_backend=token_backend
            )
            
            # Set scopes directly
            self.microsoft_account.connection.scopes = [
                'offline_access',
                'Calendars.ReadWrite',
                'Calendars.Read',
                'Calendars.Read.Shared',
                'User.Read'
            ]
            
            # Try to load existing token or authenticate
            try:
                token = self.token_manager.load_token('microsoft')
                if token:
                    self.microsoft_account.connection.token_backend.token = token
                    if self.microsoft_account.connection.token_backend.token.is_expired:
                        self.microsoft_account.connection.refresh_token()
                else:
                    self.microsoft_account.authenticate()
            except Exception as e:
                logger.warning(f"Failed to load/refresh Microsoft token: {e}")
                self.microsoft_account.authenticate()

            # Save new token
            try:
                token_data = self.microsoft_account.connection.token_backend.token
                self.token_manager.save_token('microsoft', token_data)
            except Exception as e:
                logger.error(f"Failed to save Microsoft token: {e}")
                
            logger.info("Successfully connected to Microsoft Calendar")
            
        except Exception as e:
            logger.error(f"Failed to setup Microsoft Calendar: {e}", exc_info=True)
            raise

    def setup_calendly(self):
        """Initialize Calendly API connection"""
        try:
            self.calendly_token = self.config.calendly_token
            # Verify token
            headers = {
                'Authorization': f'Bearer {self.calendly_token}',
                'Content-Type': 'application/json'
            }
            response = requests.get(
                'https://api.calendly.com/users/me',
                headers=headers,
                timeout=self.config.request_timeout
            )
            response.raise_for_status()
            logger.info("Successfully connected to Calendly")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to verify Calendly token: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        before_sleep=before_sleep_log(logger, logging.INFO)
    )
    def check_availability(
        self,
        start_time: datetime,
        end_time: datetime,
        calendar_type: Union[str, CalendarType]
    ) -> bool:
        """Check if a time slot is available"""
        self._check_rate_limit()

        if isinstance(calendar_type, str):
            calendar_type = CalendarType(calendar_type.lower())

        if calendar_type == CalendarType.GOOGLE:
            if not self.google_service:
                raise RuntimeError("Google Calendar is not configured")
                
            try:
                if not self.config.calendar_id:
                    raise ValueError("Calendar ID must be explicitly set for service account")
                    
                events_result = self.google_service.events().list(
                    calendarId=self.config.calendar_id,
                    timeMin=start_time.isoformat(),
                    timeMax=end_time.isoformat(),
                    singleEvents=True
                ).execute()
                
                return len(events_result.get('items', [])) == 0
            except HttpError as e:
                logger.error(f"Google Calendar API error: {e}")
                raise

        elif calendar_type == CalendarType.MICROSOFT:
            if not self.microsoft_account:
                raise RuntimeError("Microsoft Calendar is not configured")
                
            try:
                schedule = self.microsoft_account.schedule()
                calendar = schedule.get_default_calendar()
                q = calendar.new_query('start').greater_equal(start_time)
                q.chain('and').on_attribute('end').less_equal(end_time)
                events = calendar.get_events(query=q, limit=1)
                
                return len(list(events)) == 0
            except Exception as e:
                logger.error(f"Microsoft Calendar API error: {e}")
                raise
                
        elif calendar_type == CalendarType.CALENDLY:
            if not self.calendly_token:
                raise RuntimeError("Calendly is not configured")
                
            try:
                headers = {
                    'Authorization': f'Bearer {self.calendly_token}',
                    'Content-Type': 'application/json'
                }
                
                response = requests.get(
                    'https://api.calendly.com/scheduled_events',
                    params={
                        'start_time': start_time.isoformat(),
                        'end_time': end_time.isoformat()
                    },
                    headers=headers,
                    timeout=self.config.request_timeout
                )
                response.raise_for_status()
                
                return len(response.json().get('data', [])) == 0
            except requests.exceptions.RequestException as e:
                logger.error(f"Calendly API error: {e}")
                raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        before_sleep=before_sleep_log(logger, logging.INFO)
    )
    def create_appointment(
        self,
        appointment_data: AppointmentData,
        calendar_type: Union[str, CalendarType]
    ) -> Dict[str, Any]:
        """Create an appointment"""
        self._check_rate_limit()

        if isinstance(calendar_type, str):
            calendar_type = CalendarType(calendar_type.lower())

        if not self.check_availability(
            appointment_data.start_time,
            appointment_data.end_time,
            calendar_type
        ):
            raise ValueError("Time slot is not available")

        if calendar_type == CalendarType.GOOGLE:
            return self._create_google_appointment(appointment_data)
        elif calendar_type == CalendarType.MICROSOFT:
            return self._create_microsoft_appointment(appointment_data)
        elif calendar_type == CalendarType.CALENDLY:
            return self._create_calendly_appointment(appointment_data)

    def get_availability(
        self,
        start_date: datetime,
        end_date: datetime,
        calendar_type: Union[str, CalendarType],
        options: AvailabilityOptions,
        duration_minutes: int
    ) -> List[Dict[str, Any]]:
        """Get available time slots between start_date and end_date."""
        if isinstance(calendar_type, str):
            calendar_type = CalendarType(calendar_type.lower())

        if calendar_type == CalendarType.GOOGLE:
            if not self.google_service:
                raise RuntimeError("Google Calendar is not configured")
            if not self.config.calendar_id:
                raise ValueError("Calendar ID must be explicitly set for service account")

            try:
                # Get all events in the date range
                events_result = self.google_service.events().list(
                    calendarId=self.config.calendar_id,
                    timeMin=start_date.isoformat(),
                    timeMax=end_date.isoformat(),
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
                
                events = events_result.get('items', [])
                
                # Convert events to busy periods
                busy_periods = []
                for event in events:
                    if 'dateTime' not in event['start'] or 'dateTime' not in event['end']:
                        continue
                        
                    start = datetime.fromisoformat(event['start']['dateTime'].replace('Z', '+00:00'))
                    end = datetime.fromisoformat(event['end']['dateTime'].replace('Z', '+00:00'))
                    
                    # Add buffer time if specified
                    if options.buffer_minutes > 0:
                        start = start - timedelta(minutes=options.buffer_minutes)
                        end = end + timedelta(minutes=options.buffer_minutes)
                        
                    busy_periods.append({'start': start, 'end': end})

                # Find available slots
                available_slots = []
                current_date = start_date
                
                while current_date < end_date:
                    # Convert dates to datetime for comparison
                    current_dt = current_date if isinstance(current_date, datetime) else datetime.combine(current_date, datetime.min.time())
                    
                    # Skip excluded dates
                    if any(current_dt.date() == (d.date() if isinstance(d, datetime) else d) 
                        for d in options.excluded_dates):
                        current_date += timedelta(days=1)
                        current_date = current_date.replace(hour=0, minute=0, second=0, microsecond=0)
                        continue

                    # Check for custom availability
                    if options.custom_availability:
                        custom_day = None
                        for d, hours in options.custom_availability.items():
                            d_date = d.date() if isinstance(d, datetime) else d
                            if current_dt.date() == d_date:
                                custom_day = hours
                                break
                        
                        if custom_day:
                            day_start_str = custom_day['start']
                            day_end_str = custom_day['end']
                        else:
                            # Use regular working hours
                            day_name = current_date.strftime('%A').lower()
                            if day_name not in options.working_hours:
                                current_date += timedelta(days=1)
                                current_date = current_date.replace(hour=0, minute=0, second=0, microsecond=0)
                                continue
                            day_start_str = options.working_hours[day_name]['start']
                            day_end_str = options.working_hours[day_name]['end']
                    else:
                        # Use regular working hours
                        day_name = current_date.strftime('%A').lower()
                        if day_name not in options.working_hours:
                            current_date += timedelta(days=1)
                            current_date = current_date.replace(hour=0, minute=0, second=0, microsecond=0)
                            continue
                        day_start_str = options.working_hours[day_name]['start']
                        day_end_str = options.working_hours[day_name]['end']

                    # Convert working hours to datetime
                    day_start_time = datetime.strptime(day_start_str, '%H:%M').time()
                    day_end_time = datetime.strptime(day_end_str, '%H:%M').time()
                    
                    day_start = datetime.combine(current_date.date(), day_start_time)
                    day_end = datetime.combine(current_date.date(), day_end_time)
                    
                    # Localize times
                    tz = pytz.timezone(options.timezone)
                    if not day_start.tzinfo:
                        day_start = tz.localize(day_start)
                    if not day_end.tzinfo:
                        day_end = tz.localize(day_end)

                    # Validate duration
                    if options.min_duration_minutes and duration_minutes < options.min_duration_minutes:
                        raise ValueError(f"Duration {duration_minutes} is less than minimum {options.min_duration_minutes}")
                    if options.max_duration_minutes and duration_minutes > options.max_duration_minutes:
                        raise ValueError(f"Duration {duration_minutes} exceeds maximum {options.max_duration_minutes}")

                    # Find available slots for the day
                    time_slot = day_start
                    while time_slot + timedelta(minutes=duration_minutes) <= day_end:
                        slot_end = time_slot + timedelta(minutes=duration_minutes)
                        
                        # Check if slot overlaps with any busy period
                        is_available = True
                        for busy in busy_periods:
                            if (time_slot < busy['end'] and slot_end > busy['start']):
                                is_available = False
                                break
                        
                        if is_available:
                            available_slots.append({
                                'start': time_slot,
                                'end': slot_end,
                                'duration_minutes': duration_minutes,
                                'timezone': options.timezone
                            })
                        
                        time_slot += timedelta(minutes=duration_minutes)
                    
                    current_date += timedelta(days=1)
                    current_date = current_date.replace(hour=0, minute=0, second=0, microsecond=0)

                return available_slots

            except HttpError as e:
                logger.error(f"Google Calendar API error: {e}")
                raise
        
        else:
            raise ValueError(f"Calendar type {calendar_type} not supported for availability check")

    def create_event(
        self,
        appointment_data: AppointmentData,
        calendar_type: Union[str, CalendarType],
        options: EventOptions
    ) -> Dict[str, Any]:
        """Create a calendar event with the specified options"""
        if isinstance(calendar_type, str):
            calendar_type = CalendarType(calendar_type.lower())

        if calendar_type == CalendarType.GOOGLE:
            if not self.google_service:
                raise RuntimeError("Google Calendar is not configured")
            if not self.config.calendar_id:
                raise ValueError("Calendar ID must be explicitly set for service account")

            try:
                # Prepare event description
                description = appointment_data.description
                if options.add_attendees_to_description and appointment_data.attendees:
                    description = (
                        f"{description}\n\n"
                        f"Attendees:\n"
                        f"{', '.join(appointment_data.attendees)}"
                    )

                # Create base event
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

                # Add attendees if specified
                if options.include_attendees:
                    event['attendees'] = [{'email': attendee} for attendee in appointment_data.attendees]

                # Add reminders if enabled
                if options.reminders_enabled:
                    if options.custom_reminders:
                        # Validate reminder methods
                        for reminder in options.custom_reminders:
                            if 'method' not in reminder:
                                reminder['method'] = 'popup'  # Default to popup if not specified
                            if reminder['method'] not in ['email', 'popup']:
                                reminder['method'] = 'popup'  # Default to popup if invalid method
                        
                        event['reminders'] = {
                            'useDefault': False,
                            'overrides': options.custom_reminders
                        }
                    else:
                        event['reminders'] = {'useDefault': True}

                # Create the event
                create_kwargs = {
                    'calendarId': self.config.calendar_id,
                    'body': event
                }
                
                if options.send_updates:
                    create_kwargs['sendUpdates'] = options.send_updates

                created_event = self.google_service.events().insert(**create_kwargs).execute()
                logger.info(f"Successfully created event: {created_event.get('id')}")
                return created_event

            except HttpError as e:
                logger.error(f"Google Calendar API error: {e}")
                raise

    def _create_google_appointment(self, appointment_data: AppointmentData) -> Dict[str, Any]:
        """Create appointment in Google Calendar"""
        if not self.google_service:
            raise RuntimeError("Google Calendar is not configured")
        if not self.config.calendar_id:
            raise ValueError("Calendar ID must be explicitly set for service account")

        try:
            # Create event without attendee invitations
            event = {
                'summary': appointment_data.title,
                'description': (
                    f"{appointment_data.description}\n\n"
                    f"Please invite the following attendees manually:\n"
                    f"{', '.join(appointment_data.attendees)}"
                ),
                'start': {
                    'dateTime': appointment_data.start_time.isoformat(),
                    'timeZone': appointment_data.timezone,
                },
                'end': {
                    'dateTime': appointment_data.end_time.isoformat(),
                    'timeZone': appointment_data.timezone,
                },
                'reminders': {
                    'useDefault': True
                }
            }
            
            created_event = self.google_service.events().insert(
                calendarId=self.config.calendar_id,
                body=event
            ).execute()
            
            logger.info(f"Successfully created event with ID: {created_event.get('id')}")
            return created_event
            
        except HttpError as e:
            logger.error(f"Google Calendar API error: {e}")
            raise

    def _create_microsoft_appointment(self, appointment_data: AppointmentData) -> Dict[str, Any]:
        """Create appointment in Microsoft Calendar"""
        if not self.microsoft_account:
            raise RuntimeError("Microsoft Calendar is not configured")

        try:
            schedule = self.microsoft_account.schedule()
            calendar = schedule.get_default_calendar()
            
            event = calendar.new_event()
            event.subject = appointment_data.title
            event.body = appointment_data.description
            event.start = appointment_data.start_time
            event.end = appointment_data.end_time
            event.attendees.add(appointment_data.attendees)
            event.save()
            
            return event.to_api_data()
        except Exception as e:
            logger.error(f"Microsoft Calendar API error: {e}")
            raise

    def _create_calendly_appointment(self, appointment_data: AppointmentData) -> Dict[str, Any]:
        """Create appointment in Calendly"""
        if not self.calendly_token:
            raise RuntimeError("Calendly is not configured")

        try:
            headers = {
                'Authorization': f'Bearer {self.calendly_token}',
                'Content-Type': 'application/json'
            }
            
            payload = {
                'start_time': appointment_data.start_time.isoformat(),
                'end_time': appointment_data.end_time.isoformat(),
                'name': appointment_data.title,
                'description': appointment_data.description,
                'invitees': appointment_data.attendees
            }
            
            response = requests.post(
                'https://api.calendly.com/scheduled_events',
                json=payload,
                headers=headers,
                timeout=self.config.request_timeout
            )
            response.raise_for_status()
            
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Calendly API error: {e}")
            raise

    def update_appointment(
        self,
        event_id: str,
        calendar_type: Union[str, CalendarType],
        appointment_data: AppointmentData
    ) -> Dict[str, Any]:
        """Update an existing appointment"""
        self._check_rate_limit()

        if isinstance(calendar_type, str):
            calendar_type = CalendarType(calendar_type.lower())

        try:
            if calendar_type == CalendarType.GOOGLE:
                return self._update_google_appointment(event_id, appointment_data)
            elif calendar_type == CalendarType.MICROSOFT:
                return self._update_microsoft_appointment(event_id, appointment_data)
            elif calendar_type == CalendarType.CALENDLY:
                # Calendly doesn't support direct updates
                return self._update_calendly_appointment(event_id, appointment_data)
        except Exception as e:
            logger.error(f"Failed to update appointment: {e}")
            raise

    def _update_google_appointment(self, event_id: str, appointment_data: AppointmentData) -> Dict[str, Any]:
        """Update appointment in Google Calendar"""
        if not self.google_service:
            raise RuntimeError("Google Calendar is not configured")
        if not self.config.calendar_id:
            raise ValueError("Calendar ID must be explicitly set for service account")

        try:
            event = {
                'summary': appointment_data.title,
                'description': (
                    f"{appointment_data.description}\n\n"
                    f"Please invite the following attendees manually:\n"
                    f"{', '.join(appointment_data.attendees)}"
                ),
                'start': {
                    'dateTime': appointment_data.start_time.isoformat(),
                    'timeZone': appointment_data.timezone,
                },
                'end': {
                    'dateTime': appointment_data.end_time.isoformat(),
                    'timeZone': appointment_data.timezone,
                }
            }
            
            return self.google_service.events().update(
                calendarId=self.config.calendar_id,
                eventId=event_id,
                body=event
            ).execute()
        except HttpError as e:
            logger.error(f"Google Calendar API error: {e}")
            raise

    def _update_microsoft_appointment(self, event_id: str, appointment_data: AppointmentData) -> Dict[str, Any]:
        """Update appointment in Microsoft Calendar"""
        if not self.microsoft_account:
            raise RuntimeError("Microsoft Calendar is not configured")

        try:
            schedule = self.microsoft_account.schedule()
            calendar = schedule.get_default_calendar()
            event = calendar.get_event(event_id)
            
            event.subject = appointment_data.title
            event.body = appointment_data.description
            event.start = appointment_data.start_time
            event.end = appointment_data.end_time
            event.attendees.clear()
            event.attendees.add(appointment_data.attendees)
            event.save()
            
            return event.to_api_data()
        except Exception as e:
            logger.error(f"Microsoft Calendar API error: {e}")
            raise

    def _update_calendly_appointment(self, event_id: str, appointment_data: AppointmentData) -> Dict[str, Any]:
        """Update appointment in Calendly (cancel and recreate)"""
        if not self.calendly_token:
            raise RuntimeError("Calendly is not configured")

        try:
            # First cancel the existing appointment
            self.cancel_appointment(event_id, CalendarType.CALENDLY, notify_attendees=True)
            # Then create a new one
            return self._create_calendly_appointment(appointment_data)
        except requests.exceptions.RequestException as e:
            logger.error(f"Calendly API error: {e}")
            raise

    def cancel_appointment(
        self,
        event_id: str,
        calendar_type: Union[str, CalendarType],
        notify_attendees: bool = True
    ) -> bool:
        """Cancel an appointment"""
        self._check_rate_limit()

        if isinstance(calendar_type, str):
            calendar_type = CalendarType(calendar_type.lower())

        try:
            if calendar_type == CalendarType.GOOGLE:
                return self._cancel_google_appointment(event_id, notify_attendees)
            elif calendar_type == CalendarType.MICROSOFT:
                return self._cancel_microsoft_appointment(event_id, notify_attendees)
            elif calendar_type == CalendarType.CALENDLY:
                return self._cancel_calendly_appointment(event_id)
        except Exception as e:
            logger.error(f"Failed to cancel appointment: {e}")
            raise

    def _cancel_google_appointment(self, event_id: str, notify_attendees: bool) -> bool:
        """Cancel appointment in Google Calendar"""
        if not self.google_service:
            raise RuntimeError("Google Calendar is not configured")
        if not self.config.calendar_id:
            raise ValueError("Calendar ID must be explicitly set for service account")

        try:
            self.google_service.events().delete(
                calendarId=self.config.calendar_id,
                eventId=event_id
            ).execute()
            return True
        except HttpError as e:
            logger.error(f"Google Calendar API error: {e}")
            raise

    def _cancel_microsoft_appointment(self, event_id: str, notify_attendees: bool) -> bool:
        """Cancel appointment in Microsoft Calendar"""
        if not self.microsoft_account:
            raise RuntimeError("Microsoft Calendar is not configured")

        try:
            schedule = self.microsoft_account.schedule()
            calendar = schedule.get_default_calendar()
            event = calendar.get_event(event_id)
            
            if notify_attendees:
                event.cancel_meeting = True
            event.delete()
            return True
        except Exception as e:
            logger.error(f"Microsoft Calendar API error: {e}")
            raise

    def _cancel_calendly_appointment(self, event_id: str) -> bool:
        """Cancel appointment in Calendly"""
        if not self.calendly_token:
            raise RuntimeError("Calendly is not configured")

        try:
            headers = {
                'Authorization': f'Bearer {self.calendly_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.delete(
                f'https://api.calendly.com/scheduled_events/{event_id}',
                headers=headers,
                timeout=self.config.request_timeout
            )
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Calendly API error: {e}")
            raise

    def cleanup(self):
        """Cleanup resources"""
        try:
            if self.google_service:
                self.google_service.close()
            
            self.microsoft_account = None
            self.calendly_token = None
            self.executor.shutdown(wait=True)
            logger.info("Cleaned up calendar connections")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            raise

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.cleanup()