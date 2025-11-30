# src/services/datetime_parser_service.py

import logging
from typing import Dict, Optional
from datetime import datetime, timedelta, time, date
import pytz
from openai import AsyncOpenAI
import json
import os

logger = logging.getLogger(__name__)

class DateTimeParserService:
    """AI-powered date/time parsing - no hardcoded keywords needed"""
    
    def __init__(self):
        self.default_timezone = "UTC"
        self.client = AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    
    async def parse_user_datetime(
        self,
        user_input: str,
        user_timezone: str = None,
        business_hours: Dict = None
    ) -> Dict:
        """
        Use AI to intelligently extract date/time from natural language
        No keyword matching - GPT figures it out
        
        Args:
            user_input: User's message
            user_timezone: User's timezone
            business_hours: {"start": "09:00", "end": "18:00"}
        
        Returns:
            {
                'date': '2025-12-01',
                'time': '14:00',
                'datetime_iso': '2025-12-01T14:00:00-05:00',
                'parsed_successfully': True,
                'user_friendly': 'Monday, December 1st at 2:00 PM'
            }
        """
        
        try:
            tz = pytz.timezone(user_timezone or self.default_timezone)
            now = datetime.now(tz)
            
            # Ask GPT to extract date/time intelligently
            prompt = f"""Extract date and/or time from user's message.

**Current Context:**
- Today: {now.strftime('%A, %B %d, %Y')}
- Current time: {now.strftime('%H:%M')}
- Business hours: {business_hours.get('start', '09:00')} - {business_hours.get('end', '18:00')}

**User said:** "{user_input}"

**Your task:** Determine if user mentioned a date/time. If yes, extract it.

**Special cases:**
- "now" / "immediately" / "asap" → Next available slot (current time + 30 min if in business hours, else next business day at opening)
- "tomorrow" → {(now + timedelta(days=1)).strftime('%Y-%m-%d')}
- "today" → {now.strftime('%Y-%m-%d')}
- Day names (Monday, Tuesday, etc.) → Next occurrence of that day
- Relative times (morning, afternoon, evening) → 10:00, 14:00, 18:00

**Respond with JSON ONLY:**
{{
    "has_datetime": true or false,
    "date": "YYYY-MM-DD" or null,
    "time": "HH:MM" (24-hour) or null,
    "is_now": true or false,
    "reasoning": "brief explanation"
}}

**Examples:**
Input: "tomorrow at 2pm"
Output: {{"has_datetime": true, "date": "{(now + timedelta(days=1)).strftime('%Y-%m-%d')}", "time": "14:00", "is_now": false, "reasoning": "User wants tomorrow afternoon"}}

Input: "book it now"
Output: {{"has_datetime": true, "date": "{now.strftime('%Y-%m-%d')}", "time": "{(now + timedelta(minutes=30)).strftime('%H:%M')}", "is_now": true, "reasoning": "User wants immediate booking"}}

Input: "okay sure"
Output: {{"has_datetime": false, "date": null, "time": null, "is_now": false, "reasoning": "No date or time mentioned"}}

Input: "next Monday morning"
Output: {{"has_datetime": true, "date": "...", "time": "10:00", "is_now": false, "reasoning": "Next Monday at 10 AM"}}

Respond with ONLY the JSON, no other text."""

            # Call GPT-4o-mini (fast and cheap for this task)
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=200
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # Handle markdown code blocks if GPT returns them
            if result_text.startswith('```'):
                result_text = result_text.split('```')[1]
                if result_text.startswith('json'):
                    result_text = result_text[4:]
                result_text = result_text.strip()
            
            result = json.loads(result_text)
            
            logger.info(f"🤖 AI datetime analysis: {result.get('reasoning')}")
            
            # If no datetime found, return early
            if not result.get('has_datetime'):
                return {
                    'parsed_successfully': False,
                    'original_input': user_input,
                    'ai_reasoning': result.get('reasoning')
                }
            
            # Parse the AI-extracted date/time
            parsed_date = None
            parsed_time = None
            
            if result.get('date'):
                try:
                    parsed_date = datetime.strptime(result['date'], '%Y-%m-%d').date()
                except:
                    logger.warning(f"Could not parse AI-provided date: {result['date']}")
            
            if result.get('time'):
                try:
                    parsed_time = datetime.strptime(result['time'], '%H:%M').time()
                except:
                    logger.warning(f"Could not parse AI-provided time: {result['time']}")
            
            # Handle "now" scenario
            if result.get('is_now') and business_hours:
                start_time = datetime.strptime(business_hours['start'], '%H:%M').time()
                end_time = datetime.strptime(business_hours['end'], '%H:%M').time()
                current_time = now.time()
                
                if start_time <= current_time <= end_time:
                    # Within business hours - schedule 30 min from now
                    next_slot = now + timedelta(minutes=30)
                    parsed_date = next_slot.date()
                    parsed_time = next_slot.time()
                    logger.info(f"'now' → {parsed_date} at {parsed_time}")
                else:
                    # After hours - schedule next business day at opening
                    if current_time > end_time:
                        parsed_date = (now + timedelta(days=1)).date()
                    else:
                        parsed_date = now.date()
                    parsed_time = start_time
                    logger.info(f"After hours 'now' → {parsed_date} at {parsed_time}")
            
            # If we have both date and time, return success
            if parsed_date and parsed_time:
                dt = datetime.combine(parsed_date, parsed_time)
                dt = tz.localize(dt)
                
                # Validate business hours
                within_hours = True
                if business_hours:
                    start_time = datetime.strptime(business_hours['start'], '%H:%M').time()
                    end_time = datetime.strptime(business_hours['end'], '%H:%M').time()
                    within_hours = start_time <= parsed_time <= end_time
                
                return {
                    'date': parsed_date.strftime('%Y-%m-%d'),
                    'time': parsed_time.strftime('%H:%M'),
                    'datetime_iso': dt.isoformat(),
                    'parsed_successfully': True,
                    'within_business_hours': within_hours,
                    'user_friendly': self._format_user_friendly(parsed_date, parsed_time),
                    'ai_reasoning': result.get('reasoning'),
                    'timezone': str(tz)
                }
            
            # Only date or only time parsed
            elif parsed_date:
                return {
                    'date': parsed_date.strftime('%Y-%m-%d'),
                    'time': None,
                    'parsed_successfully': True,
                    'needs_time': True,
                    'ai_reasoning': result.get('reasoning'),
                    'timezone': str(tz)
                }
            
            else:
                return {
                    'parsed_successfully': False,
                    'original_input': user_input,
                    'ai_reasoning': result.get('reasoning')
                }
        
        except json.JSONDecodeError as e:
            logger.error(f"AI returned invalid JSON: {result_text}")
            logger.error(f"JSON error: {str(e)}")
            return {
                'parsed_successfully': False,
                'error': 'AI parsing failed',
                'original_input': user_input
            }
        
        except Exception as e:
            logger.error(f"Error in AI datetime parsing: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                'parsed_successfully': False,
                'error': str(e),
                'original_input': user_input
            }
    
    def _format_user_friendly(self, date_obj: date, time_obj: time) -> str:
        """Format for user readability: 'Monday, December 1st at 2:00 PM'"""
        day_name = date_obj.strftime('%A')
        month_name = date_obj.strftime('%B')
        day = date_obj.day
        
        # Add ordinal suffix
        if 4 <= day <= 20 or 24 <= day <= 30:
            suffix = "th"
        else:
            suffix = ["st", "nd", "rd"][day % 10 - 1]
        
        hour = time_obj.hour
        minute = time_obj.minute
        meridiem = 'AM' if hour < 12 else 'PM'
        hour_12 = hour if hour <= 12 else hour - 12
        if hour_12 == 0:
            hour_12 = 12
        
        return f"{day_name}, {month_name} {day}{suffix} at {hour_12}:{minute:02d} {meridiem}"

# Global instance
datetime_parser_service = DateTimeParserService()