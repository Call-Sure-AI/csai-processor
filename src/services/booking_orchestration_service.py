# src/services/booking_orchestration_service.py

import logging
from typing import Dict, Optional, List
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

class BookingState(Enum):
    """Booking conversation states"""
    INITIAL = "initial"
    COLLECTING_DATE = "collecting_date"
    COLLECTING_TIME = "collecting_time"
    CHECKING_AVAILABILITY = "checking_availability"
    COLLECTING_EMAIL = "collecting_email"
    VERIFYING_EMAIL = "verifying_email"
    CONFIRMING_BOOKING = "confirming_booking"
    COMPLETED = "completed"
    FAILED = "failed"

class BookingOrchestrationService:
    """State machine for managing booking flow"""
    
    def __init__(self):
        self.booking_sessions = {}  # call_sid -> booking_state
    
    def initialize_booking(
        self,
        call_sid: str,
        customer_name: str,
        customer_phone: str,
        campaign_id: str
    ) -> Dict:
        """Initialize a new booking session"""
        
        session = {
            'state': BookingState.INITIAL,
            'customer_name': customer_name,
            'customer_phone': customer_phone,
            'campaign_id': campaign_id,
            'collected_data': {
                'date': None,
                'time': None,
                'datetime_iso': None,
                'email': None,
                'email_verified': False,
                'slot_checked': False,
                'slot_available': False
            },
            'history': [],
            'created_at': datetime.utcnow().isoformat()
        }
        
        self.booking_sessions[call_sid] = session
        logger.info(f"Booking session initialized for {call_sid}")
        
        return session
    
    def get_session(self, call_sid: str) -> Optional[Dict]:
        """Get existing booking session"""
        return self.booking_sessions.get(call_sid)
    
    def update_session_data(
        self,
        call_sid: str,
        field: str,
        value: any
    ):
        """Update a specific field in booking session"""
        if call_sid in self.booking_sessions:
            self.booking_sessions[call_sid]['collected_data'][field] = value
            logger.info(f"Updated {field} = {value} for {call_sid}")
    
    def transition_state(
        self,
        call_sid: str,
        new_state: BookingState,
        reason: str = None
    ):
        """Transition to new state"""
        if call_sid in self.booking_sessions:
            old_state = self.booking_sessions[call_sid]['state']
            self.booking_sessions[call_sid]['state'] = new_state
            self.booking_sessions[call_sid]['history'].append({
                'from': old_state.value,
                'to': new_state.value,
                'reason': reason,
                'timestamp': datetime.utcnow().isoformat()
            })
            logger.info(f"State: {old_state.value} → {new_state.value} ({reason})")
    
    def get_next_action(self, call_sid: str) -> Dict:
        """Determine what to ask/do next based on current state"""
        
        session = self.get_session(call_sid)
        if not session:
            return {'action': 'error', 'message': 'No session found'}
        
        state = session['state']
        collected = session['collected_data']
        
        # State machine logic
        if state == BookingState.INITIAL:
            if not collected['date']:
                return {
                    'action': 'ask',
                    'field': 'date',
                    'prompt_hint': 'Ask for preferred date'
                }
        
        elif state == BookingState.COLLECTING_DATE:
            if collected['date'] and not collected['time']:
                return {
                    'action': 'ask',
                    'field': 'time',
                    'prompt_hint': 'Ask for preferred time'
                }
            elif collected['date'] and collected['time']:
                return {
                    'action': 'function_call',
                    'function': 'check_slot_availability',
                    'prompt_hint': 'Check if slot is available'
                }
        
        elif state == BookingState.CHECKING_AVAILABILITY:
            if collected['slot_available'] and not collected['email']:
                return {
                    'action': 'ask',
                    'field': 'email',
                    'prompt_hint': 'Ask for email address'
                }
            elif not collected['slot_available']:
                return {
                    'action': 'suggest_alternatives',
                    'prompt_hint': 'Suggest alternative times'
                }
        
        elif state == BookingState.COLLECTING_EMAIL:
            if collected['email'] and not collected['email_verified']:
                return {
                    'action': 'function_call',
                    'function': 'verify_customer_email',
                    'prompt_hint': 'Verify email by spelling it out'
                }
        
        elif state == BookingState.VERIFYING_EMAIL:
            if collected['email_verified']:
                return {
                    'action': 'function_call',
                    'function': 'create_booking',
                    'prompt_hint': 'Create the booking now'
                }
        
        elif state == BookingState.COMPLETED:
            return {
                'action': 'close',
                'prompt_hint': 'Booking complete, ask if anything else needed'
            }
        
        return {'action': 'unknown', 'prompt_hint': 'Continue conversation'}
    
    def is_booking_active(self, call_sid: str) -> bool:
        """Check if there's an active booking session"""
        session = self.get_session(call_sid)
        if not session:
            return False
        
        return session['state'] not in [BookingState.COMPLETED, BookingState.FAILED]
    
    def can_create_booking(self, call_sid: str) -> bool:
        """Check if all required info is collected"""
        session = self.get_session(call_sid)
        if not session:
            return False
        
        collected = session['collected_data']
        
        return all([
            collected['date'],
            collected['time'],
            collected['email'],
            collected['email_verified'],
            collected['slot_available']
        ])
    
    def clear_session(self, call_sid: str):
        """Clear booking session"""
        if call_sid in self.booking_sessions:
            del self.booking_sessions[call_sid]
            logger.info(f"Cleared booking session for {call_sid}")

# Global instance
booking_orchestrator = BookingOrchestrationService()