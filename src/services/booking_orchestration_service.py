# src/services/booking_orchestration_service.py

from enum import Enum
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)

class BookingState(Enum):
    """Booking conversation states"""
    INITIAL = "initial"
    COLLECTING_DATE = "collecting_date"
    COLLECTING_TIME = "collecting_time"
    CHECKING_AVAILABILITY = "checking_availability"
    COLLECTING_EMAIL = "collecting_email"  # ✅ EMAIL IS REQUIRED
    VERIFYING_EMAIL = "verifying_email"
    CONFIRMING_BOOKING = "confirming_booking"
    COMPLETED = "completed"

class BookingOrchestrationService:
    """Manages booking flow - asks for email (we have name+phone already)"""
    
    def __init__(self):
        self.sessions: Dict[str, Dict] = {}
    
    def initialize_booking(
        self,
        call_sid: str,
        customer_name: str,
        customer_phone: str,
        campaign_id: str
    ) -> Dict:
        """Initialize booking with pre-populated name and phone"""
        
        session = {
            'call_sid': call_sid,
            'state': BookingState.INITIAL,
            'customer_info': {
                'name': customer_name,      # ✅ Already have from curl
                'phone': customer_phone,    # ✅ Already have from curl
                'email': None,              # ❌ NEED to ask for this
                'campaign_id': campaign_id
            },
            'collected_data': {
                'date': None,               # ❌ NEED to ask
                'time': None,               # ❌ NEED to ask
                'datetime_iso': None,
                'slot_available': False,
                'email_verified': False
            },
            'history': []
        }
        
        self.sessions[call_sid] = session
        logger.info(f"🎫 Booking initialized for {customer_name} ({customer_phone})")
        logger.info(f"   Need to collect: date, time, email")
        
        return session
    
    def get_session(self, call_sid: str) -> Optional[Dict]:
        """Get existing booking session"""
        return self.sessions.get(call_sid)
    
    def update_session_data(self, call_sid: str, key: str, value: any):
        """Update collected data"""
        if call_sid in self.sessions:
            if key == 'email':
                # Store email in customer_info, not collected_data
                self.sessions[call_sid]['customer_info']['email'] = value
                logger.info(f"📧 Email: {value}")
            else:
                self.sessions[call_sid]['collected_data'][key] = value
                logger.info(f"📝 {key} = {value}")
    
    def transition_state(self, call_sid: str, new_state: BookingState, reason: str = ""):
        """Move to next state"""
        if call_sid in self.sessions:
            old_state = self.sessions[call_sid]['state']
            self.sessions[call_sid]['state'] = new_state
            
            self.sessions[call_sid]['history'].append({
                'from': old_state.value,
                'to': new_state.value,
                'reason': reason
            })
            
            logger.info(f"🔄 {old_state.value} → {new_state.value} ({reason})")
    
    def get_next_action(self, call_sid: str) -> Dict:
        """Determine what to ask for next"""
        if call_sid not in self.sessions:
            return {'action': 'initialize', 'prompt_hint': 'Start booking'}
        
        session = self.sessions[call_sid]
        state = session['state']
        collected = session['collected_data']
        customer = session['customer_info']
        
        # Priority order: date → time → check availability → email → confirm
        
        # 1. Need date?
        if state in [BookingState.INITIAL, BookingState.COLLECTING_DATE]:
            if not collected['date']:
                return {
                    'action': 'collect_date',
                    'prompt_hint': 'Ask: "What date works for you?"'
                }
        
        # 2. Need time?
        if state == BookingState.COLLECTING_TIME:
            if not collected['time']:
                return {
                    'action': 'collect_time',
                    'prompt_hint': 'Ask: "What time would you prefer?"'
                }
        
        # 3. Check availability?
        if state == BookingState.CHECKING_AVAILABILITY:
            if collected['date'] and collected['time']:
                return {
                    'action': 'check_slot',
                    'prompt_hint': 'Call check_slot_availability function'
                }
        
        # 4. Need email? (only ask AFTER slot is confirmed available)
        if state == BookingState.COLLECTING_EMAIL:
            if not customer['email']:
                return {
                    'action': 'collect_email',
                    'prompt_hint': 'Ask: "What\'s your email address for the confirmation?"'
                }
        
        # 5. Verify email?
        if state == BookingState.VERIFYING_EMAIL:
            if customer['email'] and not collected['email_verified']:
                return {
                    'action': 'verify_email',
                    'prompt_hint': 'Call verify_customer_email function'
                }
        
        # 6. Create booking?
        if state == BookingState.CONFIRMING_BOOKING:
            if collected['slot_available'] and customer['email']:
                return {
                    'action': 'create_booking',
                    'prompt_hint': 'Call create_booking to finalize'
                }
        
        return {'action': 'continue', 'prompt_hint': 'Keep conversation going'}
    
    def is_booking_active(self, call_sid: str) -> bool:
        """Check if booking in progress"""
        if call_sid not in self.sessions:
            return False
        return self.sessions[call_sid]['state'] != BookingState.COMPLETED
    
    def can_create_booking(self, call_sid: str) -> bool:
        """Check if we have everything needed to create booking"""
        if call_sid not in self.sessions:
            return False
        
        session = self.sessions[call_sid]
        collected = session['collected_data']
        customer = session['customer_info']
        
        # Need: name (have), phone (have), email (ask), date (ask), time (ask)
        return all([
            customer['name'],           # ✅ Have from curl
            customer['phone'],          # ✅ Have from curl
            customer['email'],          # ❌ Must collect
            collected['date'],          # ❌ Must collect
            collected['time'],          # ❌ Must collect
            collected['slot_available'] # ✅ System checks
        ])
    
    def clear_session(self, call_sid: str):
        """Remove booking session"""
        if call_sid in self.sessions:
            del self.sessions[call_sid]
            logger.info(f"🗑️ Cleared: {call_sid}")

# Global instance
booking_orchestrator = BookingOrchestrationService()