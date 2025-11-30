# src/services/datetime_parser_service.py

import logging
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
import pytz
from dateutil import parser as date_parser
import re

logger = logging.getLogger(__name__)

class DateTimeParserService:
    """Intelligent date/time parsing with timezone awareness"""
    
    def __init__(self):
        self.default_timezone = "UTC"
    
    async def parse_user_datetime(
        self,
        user_input: str,
        user_timezone: str = None,
        business_hours: Dict = None
    ) -> Dict:
        """
        Parse natural language date/time to structured format
        
        Args:
            user_input: "tomorrow at 2pm", "next Monday 10am", "December 1st"
            user_timezone: User's timezone (e.g., "America/New_York")
            business_hours: {"start": "09:00", "end": "18:00", "timezone": "America/Chicago"}
        
        Returns:
            {
                'date': '2025-12-01',
                'time': '14:00',
                'datetime_iso': '2025-12-01T14:00:00-05:00',
                'parsed_successfully': True,
                'within_business_hours': True,
                'suggested_alternatives': [...] if outside hours
            }
        """
        
        try:
            # Get timezone
            tz = pytz.timezone(user_timezone or self.default_timezone)
            now = datetime.now(tz)
            
            user_input_lower = user_input.lower().strip()
            
            # Parse relative dates
            parsed_date = None
            parsed_time = None
            
            # Tomorrow
            if 'tomorrow' in user_input_lower:
                parsed_date = (now + timedelta(days=1)).date()
            
            # Today
            elif 'today' in user_input_lower:
                parsed_date = now.date()
            
            # Next week
            elif 'next week' in user_input_lower:
                parsed_date = (now + timedelta(weeks=1)).date()
            
            # Day names (next Monday, etc.)
            elif any(day in user_input_lower for day in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']):
                parsed_date = self._parse_day_name(user_input_lower, now)
            
            # Try dateutil parser for absolute dates
            else:
                try:
                    parsed_dt = date_parser.parse(user_input, fuzzy=True)
                    # If year not specified, assume current year
                    if parsed_dt.year == datetime.now().year or parsed_dt.year < datetime.now().year:
                        parsed_dt = parsed_dt.replace(year=now.year)
                    parsed_date = parsed_dt.date()
                    if parsed_dt.hour != 0 or parsed_dt.minute != 0:
                        parsed_time = parsed_dt.time()
                except:
                    logger.warning(f"Could not parse date from: {user_input}")
            
            # Parse time if not already parsed
            if not parsed_time:
                parsed_time = self._parse_time(user_input_lower)
            
            # Validate against business hours
            if business_hours and parsed_time:
                validation = self._validate_business_hours(
                    parsed_time,
                    business_hours,
                    parsed_date
                )
            else:
                validation = {'within_hours': True, 'suggested_alternatives': []}
            
            if parsed_date and parsed_time:
                # Combine date and time with timezone
                dt = datetime.combine(parsed_date, parsed_time)
                dt = tz.localize(dt)
                
                return {
                    'date': parsed_date.strftime('%Y-%m-%d'),
                    'time': parsed_time.strftime('%H:%M'),
                    'datetime_iso': dt.isoformat(),
                    'parsed_successfully': True,
                    'within_business_hours': validation['within_hours'],
                    'suggested_alternatives': validation.get('suggested_alternatives', []),
                    'timezone': str(tz),
                    'user_friendly': self._format_user_friendly(parsed_date, parsed_time)
                }
            
            elif parsed_date:
                # Only date parsed, no time
                return {
                    'date': parsed_date.strftime('%Y-%m-%d'),
                    'time': None,
                    'datetime_iso': None,
                    'parsed_successfully': True,
                    'needs_time': True,
                    'timezone': str(tz)
                }
            
            else:
                # Could not parse
                return {
                    'parsed_successfully': False,
                    'error': 'Could not understand date/time',
                    'original_input': user_input
                }
        
        except Exception as e:
            logger.error(f"Error parsing datetime: {str(e)}")
            return {
                'parsed_successfully': False,
                'error': str(e),
                'original_input': user_input
            }
    
    def _parse_day_name(self, text: str, reference_date: datetime) -> datetime.date:
        """Parse 'next Monday', 'this Friday', etc."""
        days = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6
        }
        
        for day_name, day_num in days.items():
            if day_name in text:
                days_ahead = day_num - reference_date.weekday()
                if days_ahead <= 0:  # Target day already happened this week
                    days_ahead += 7
                return (reference_date + timedelta(days=days_ahead)).date()
        
        return None
    
    def _parse_time(self, text: str) -> Optional[datetime.time]:
        """Extract time from text"""
        # Pattern: "2pm", "2:30pm", "14:00", "2 pm"
        patterns = [
            r'(\d{1,2}):(\d{2})\s*(am|pm)',  # 2:30pm
            r'(\d{1,2})\s*(am|pm)',           # 2pm
            r'(\d{1,2}):(\d{2})',             # 14:30
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                groups = match.groups()
                
                if len(groups) == 3:  # 2:30pm format
                    hour = int(groups[0])
                    minute = int(groups[1])
                    meridiem = groups[2]
                    
                    if meridiem == 'pm' and hour != 12:
                        hour += 12
                    elif meridiem == 'am' and hour == 12:
                        hour = 0
                    
                    return datetime.time(hour=hour, minute=minute)
                
                elif len(groups) == 2 and groups[1] in ['am', 'pm']:  # 2pm format
                    hour = int(groups[0])
                    meridiem = groups[1]
                    
                    if meridiem == 'pm' and hour != 12:
                        hour += 12
                    elif meridiem == 'am' and hour == 12:
                        hour = 0
                    
                    return datetime.time(hour=hour, minute=0)
                
                elif len(groups) == 2:  # 14:30 format
                    hour = int(groups[0])
                    minute = int(groups[1])
                    return datetime.time(hour=hour, minute=minute)
        
        return None
    
    def _validate_business_hours(
        self,
        time: datetime.time,
        business_hours: Dict,
        date: datetime.date
    ) -> Dict:
        """Check if time is within business hours"""
        try:
            start_time = datetime.strptime(business_hours['start'], '%H:%M').time()
            end_time = datetime.strptime(business_hours['end'], '%H:%M').time()
            
            within_hours = start_time <= time <= end_time
            
            if not within_hours:
                # Suggest alternatives
                alternatives = []
                # Suggest closest valid time
                if time < start_time:
                    alternatives.append(business_hours['start'])
                if time > end_time:
                    alternatives.append(business_hours['start'])  # Next day
                
                return {
                    'within_hours': False,
                    'suggested_alternatives': alternatives,
                    'business_start': business_hours['start'],
                    'business_end': business_hours['end']
                }
            
            return {'within_hours': True}
        
        except Exception as e:
            logger.error(f"Error validating business hours: {e}")
            return {'within_hours': True}  # Default to allowing
    
    def _format_user_friendly(self, date: datetime.date, time: datetime.time) -> str:
        """Format for user readability: 'Monday, December 1st at 2:00 PM'"""
        day_name = date.strftime('%A')
        month_name = date.strftime('%B')
        day = date.day
        
        # Add ordinal suffix (1st, 2nd, 3rd, etc.)
        if 4 <= day <= 20 or 24 <= day <= 30:
            suffix = "th"
        else:
            suffix = ["st", "nd", "rd"][day % 10 - 1]
        
        hour = time.hour
        minute = time.minute
        meridiem = 'AM' if hour < 12 else 'PM'
        hour_12 = hour if hour <= 12 else hour - 12
        if hour_12 == 0:
            hour_12 = 12
        
        return f"{day_name}, {month_name} {day}{suffix} at {hour_12}:{minute:02d} {meridiem}"

# Global instance
datetime_parser_service = DateTimeParserService()