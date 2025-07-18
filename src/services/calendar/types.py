from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from typing import Dict, List, Optional, Union, Any
from enum import Enum
import re
import pytz
import logging

logger = logging.getLogger(__name__)

class CalendarType(Enum):
    """Enum for supported calendar types"""
    GOOGLE = "google"
    MICROSOFT = "microsoft"
    CALENDLY = "calendly"

@dataclass
class CalendarConfig:
    """Configuration for calendar integration"""
    service_account_json: Optional[str] = None
    calendar_id: Optional[str] = None
    microsoft_client_id: Optional[str] = None
    microsoft_client_secret: Optional[str] = None
    calendly_token: Optional[str] = None
    timezone: str = "UTC"
    request_timeout: int = 30
    max_concurrent_operations: int = 5
    token_store_path: Optional[str] = None
    rate_limit_per_minute: int = 60
    state_file_version: str = "1.0.0"

@dataclass
class EventOptions:
    """Configuration options for event creation and updates"""
    include_attendees: bool = False  # Whether to include attendees in the API call
    send_updates: Optional[str] = None  # 'all', 'externalOnly', 'none', or None
    add_attendees_to_description: bool = True  # Whether to add attendees to description
    reminders_enabled: bool = True
    custom_reminders: Optional[List[Dict[str, int]]] = None  # List of {'minutes': X} dictionaries

@dataclass
class AvailabilityOptions:
    """Configuration options for availability checking"""
    working_hours: Dict[str, Dict[str, str]] = field(default_factory=dict)
    buffer_minutes: int = 0  # Buffer time between events
    min_duration_minutes: int = 30  # Minimum duration for available slots
    max_duration_minutes: Optional[int] = None  # Maximum duration for available slots
    timezone: str = "UTC"
    excluded_dates: List[datetime] = field(default_factory=list)  # Dates to exclude
    custom_availability: Optional[Dict[datetime, Dict[str, str]]] = None  # Custom hours for specific dates

class AppointmentData:
    """Class to validate and structure appointment data"""
    def __init__(
        self,
        start_time: datetime,
        end_time: datetime,
        title: str,
        description: str,
        attendees: List[str],
        timezone: str = "UTC",
        max_duration: Optional[timedelta] = None
    ):
        # Validate timezone
        try:
            pytz.timezone(timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            raise ValueError(f"Invalid timezone: {timezone}")

        # Validate types
        if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
            raise TypeError("Start and end times must be datetime objects")

        # Convert times to UTC for comparison
        start_utc = self._to_utc(start_time, timezone)
        end_utc = self._to_utc(end_time, timezone)
        now_utc = datetime.now(pytz.UTC)

        # Validate times
        if start_utc >= end_utc:
            raise ValueError("End time must be after start time")
            
        if start_utc < now_utc:
            raise ValueError("Cannot schedule appointments in the past")

        if max_duration and (end_utc - start_utc) > max_duration:
            raise ValueError(f"Appointment duration exceeds maximum of {max_duration}")

        # Validate title
        if not title or not title.strip():
            raise ValueError("Title cannot be empty")
        if len(title) > 200:
            raise ValueError("Title is too long (max 200 characters)")

        # Check for potentially problematic characters in title
        if any(char in title for char in ['<', '>', '&', '"', "'"]):
            logger.warning("Title contains special characters that might need escaping")

        # Validate attendees
        if not attendees:
            raise ValueError("At least one attendee is required")

        email_pattern = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
        for attendee in attendees:
            if not email_pattern.match(attendee):
                raise ValueError(f"Invalid email address: {attendee}")

        # Set attributes
        self.start_time = start_utc
        self.end_time = end_utc
        self.title = title
        self.description = description
        self.attendees = list(set(attendees))  # Remove duplicates
        self.timezone = timezone

        # Log warnings
        if start_utc.date() != end_utc.date():
            logger.warning("Appointment spans multiple days")
        if len(self.attendees) != len(attendees):
            logger.warning("Duplicate attendees were removed")

    @staticmethod
    def _to_utc(dt: datetime, timezone: str) -> datetime:
        """Convert datetime to UTC"""
        if dt.tzinfo is None:
            dt = pytz.timezone(timezone).localize(dt)
        return dt.astimezone(pytz.UTC)

    def to_local_timezone(self, timezone: str) -> Dict[str, datetime]:
        """Convert appointment times to a specific timezone"""
        tz = pytz.timezone(timezone)
        return {
            'start_time': self.start_time.astimezone(tz),
            'end_time': self.end_time.astimezone(tz)
        }