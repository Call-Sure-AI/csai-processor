# src/services/agent_tools.py
from typing import Dict, Any, List
from services.ticket_service import ticket_service
from services.booking_service import booking_service
from services.datetime_parser_service import datetime_parser_service
from services.slot_manager_service import SlotManagerService  # NEW IMPORT
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

TICKET_FUNCTIONS = [
    {
        "name": "create_ticket",
        "description": "Create a support ticket when a customer reports an issue or requests help. Use this when the customer mentions problems, issues, complaints, or needs assistance.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Brief title summarizing the issue"
                },
                "description": {
                    "type": "string",
                    "description": "Detailed description of the issue or request"
                },
                "customer_name": {
                    "type": "string",
                    "description": "Customer's name if provided"
                },
                "customer_phone": {
                    "type": "string",
                    "description": "Customer's phone number"
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                    "description": "Priority level based on urgency"
                }
            },
            "required": ["title", "description"]
        }
    },
    {
        "name": "check_slot_availability",
        "description": "Check if a time slot is available for booking. ONLY call this when you have a SPECIFIC date AND time. DO NOT call if customer asks 'what's available' - suggest times instead.",
        "parameters": {
            "type": "object",
            "properties": {
                "datetime_expression": {
                    "type": "string", 
                    "description": "Natural language date/time like 'tomorrow at 2pm', 'December 1st at 10am', 'next Monday morning'"
                }
            },
            "required": ["datetime_expression"]
        }
    },
    {
        "name": "verify_customer_email",
        "description": "Verify customer email by spelling it out for confirmation. MUST be used before booking.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_email": {"type": "string", "description": "Customer's email address to verify"}
            },
            "required": ["customer_email"]
        }
    },
    {
        "name": "create_booking",
        "description": "Schedule an appointment when customer agrees to book a time slot. Only call after slot availability is confirmed and email is verified.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "Customer's full name"},
                "customer_phone": {"type": "string", "description": "Customer's phone number"},
                "datetime_expression": {
                    "type": "string",
                    "description": "Natural language date/time like 'tomorrow at 2pm'"
                },
                "customer_email": {"type": "string", "description": "Customer's email"},
                "notes": {"type": "string", "description": "Special notes"}
            },
            "required": ["customer_name", "customer_phone", "datetime_expression"]
        }
    },
    {
        "name": "get_ticket_status",
        "description": "Get the status and details of an existing ticket when customer asks about their ticket or issue status",
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "The ticket ID (format: TKT-XXXXXX)"
                }
            },
            "required": ["ticket_id"]
        }
    }
]

async def execute_function(
    function_name: str,
    arguments: Dict[str, Any],
    company_id: str,
    call_sid: str,
    campaign_id: str = None,
    user_timezone: str = "UTC",
    business_hours: Dict = None
) -> str:
    """Execute agent function and return response"""
    try:
        logger.info(f"Executing function: {function_name} with args: {arguments}")
        
        if function_name == "create_ticket":
            result = await ticket_service.create_ticket(
                company_id=company_id,
                title=arguments.get("title"),
                description=arguments.get("description"),
                customer_name=arguments.get("customer_name"),
                customer_phone=arguments.get("customer_phone"),
                priority=arguments.get("priority", "medium"),
                tags=["voice-call", "auto-generated"],
                meta_data={
                    "call_sid": call_sid,
                    "source": "voice_agent"
                }
            )
            
            if result.get("success"):
                ticket_id = result.get("ticket_id")
                return f"I've created a support ticket for you with ID {ticket_id}. Our team will review this and get back to you shortly. Is there anything else I can help you with?"
            else:
                return "I'm having trouble creating the ticket right now, but I've noted your issue. Let me connect you with a supervisor who can help immediately."

        elif function_name == "check_slot_availability":
            # NEW: Enhanced slot availability check with capacity management
            
            logger.info(f"🔍 Checking slot availability for campaign: {campaign_id}")
            
            if not campaign_id:
                return "Unable to check availability - missing campaign information."
            
            datetime_expression = arguments.get('datetime_expression', '')
            
            if not datetime_expression:
                return "I need a specific date and time to check availability. For example: 'tomorrow at 2pm' or 'next Monday at 10am'."
            
            # AI-powered datetime parsing
            parsed = await datetime_parser_service.parse_user_datetime(
                user_input=datetime_expression,
                user_timezone=user_timezone,
                business_hours=business_hours or {'start': '09:00', 'end': '18:00'}
            )
            
            # TRIPLE-LAYER SAFETY CHECKS (preserve existing logic)
            
            # CHECK 1: Did parsing fail completely?
            if not parsed.get('parsed_successfully'):
                return "I'm having trouble understanding that date and time. Could you say it differently? For example: 'tomorrow at 2pm' or 'December 1st at 10am'."
            
            # CHECK 2: Do we have complete datetime (both date AND time)?
            if not parsed.get('datetime_iso'):
                if parsed.get('needs_time'):
                    date_str = parsed.get('date', 'that date')
                    return f"I got the date ({date_str}), but what time would you prefer? For example: 10 AM, 2 PM, or 4 PM?"
                else:
                    return "I need both a date and time to check availability. When would you like to schedule?"
            
            # CHECK 3: Is it within business hours?
            if not parsed.get('within_business_hours', True):
                user_friendly = parsed.get('user_friendly', 'that time')
                bh_start = business_hours.get('start', '9 AM')
                bh_end = business_hours.get('end', '6 PM')
                return f"I'm sorry, {user_friendly} is outside our business hours ({bh_start} - {bh_end}, Monday-Friday). Would you like to book during business hours instead?"
            
            # Now safe to use datetime_iso
            slot_start = parsed['datetime_iso']
            slot_end_dt = datetime.fromisoformat(slot_start.replace('Z', '+00:00')) + timedelta(minutes=30)
            slot_end = slot_end_dt.isoformat()
            
            logger.info(f"✓ Parsed datetime: {parsed.get('user_friendly')}")
            logger.info(f"  ISO format: {slot_start}")
            logger.info(f"  AI reasoning: {parsed.get('ai_reasoning', 'N/A')}")
            
            # NEW: Check capacity from backend using SlotManagerService
            slot_manager = SlotManagerService()
            capacity_info = await slot_manager.check_slot_capacity(
                campaign_id=campaign_id,
                slot_start=datetime.fromisoformat(slot_start.replace('Z', '+00:00')),
                slot_end=slot_end_dt
            )
            
            logger.info(f"📊 Capacity check result: {capacity_info}")
            
            # Handle capacity-aware responses
            if capacity_info.get('available'):
                available_capacity = capacity_info.get('available_capacity', 1)
                max_capacity = capacity_info.get('max_capacity', 1)
                
                if available_capacity > 0:
                    if max_capacity > 1:
                        # Multiple slots available
                        return (
                            f"Great news! {parsed.get('user_friendly')} is available. "
                            f"We have {available_capacity} of {max_capacity} spots open for that time. "
                            f"What's your email address so I can send you the confirmation?"
                        )
                    else:
                        # Single slot available
                        return (
                            f"Perfect! {parsed.get('user_friendly')} is available. "
                            f"What's your email address for the confirmation?"
                        )
                
                elif capacity_info.get('allow_overbooking'):
                    # Fully booked but overbooking allowed
                    return (
                        f"{parsed.get('user_friendly')} is fully booked ({max_capacity}/{max_capacity} spots filled), "
                        f"but I can still add you to that time slot if you'd like. What's your email?"
                    )
                else:
                    # Fully booked, no overbooking
                    return (
                        f"I'm sorry, {parsed.get('user_friendly')} is fully booked. "
                        f"All {max_capacity} spots are taken. Would you like to try a different time? "
                        f"I have availability at 10 AM, 2 PM, or 4 PM."
                    )
            else:
                # Slot not available
                return (
                    f"I'm sorry, {parsed.get('user_friendly')} is not available. "
                    f"How about tomorrow at 10 AM or 2 PM instead?"
                )
        
        elif function_name == "verify_customer_email":
            customer_email = arguments.get("customer_email")
            
            if not customer_email:
                return "I didn't catch your email address. Could you please provide it?"
            
            # Spell out email
            spelled_email = booking_service.spell_out_email(customer_email)
            
            return (
                f"Let me confirm your email address. I have: {customer_email}. "
                f"That's {spelled_email}. Is that correct?"
            )
            
        elif function_name == "create_booking":
            customer_name = arguments.get("customer_name")
            customer_phone = arguments.get("customer_phone")
            datetime_expression = arguments.get("datetime_expression")
            customer_email = arguments.get("customer_email")
            notes = arguments.get("notes", "")
            
            if not all([customer_name, customer_phone, datetime_expression]):
                return "I'm missing some information. Let me start over with the booking details."
            
            # Parse datetime for booking creation
            parsed = await datetime_parser_service.parse_user_datetime(
                user_input=datetime_expression,
                user_timezone=user_timezone,
                business_hours=business_hours or {'start': '09:00', 'end': '18:00'}
            )
            
            # Safety check
            if not parsed.get('parsed_successfully') or not parsed.get('datetime_iso'):
                return "I'm having trouble with that date and time. Let's try booking again with a specific date and time."
            
            slot_start = parsed['datetime_iso']
            start_dt = datetime.fromisoformat(slot_start.replace('Z', '+00:00'))
            slot_end = (start_dt + timedelta(minutes=30)).isoformat()
            
            logger.info(f"📅 Creating booking:")
            logger.info(f"   Customer: {customer_name}")
            logger.info(f"   Phone: {customer_phone}")
            logger.info(f"   Email: {customer_email}")
            logger.info(f"   Time: {parsed.get('user_friendly')}")
            logger.info(f"   Campaign: {campaign_id}")
            
            # Create booking
            result = await booking_service.create_booking(
                campaign_id=campaign_id,
                customer_name=customer_name,
                customer_phone=customer_phone,
                slot_start=slot_start,
                slot_end=slot_end,
                customer_email=customer_email,
                notes=notes
            )
            
            if result.get("success"):
                booking_id = result.get("booking_id")
                logger.info(f"✅ Booking created: {booking_id}")
                return (
                    f"Excellent! I've scheduled your appointment for {parsed.get('user_friendly')}. "
                    f"Your booking ID is {booking_id}. "
                    f"You'll receive a confirmation email at {customer_email} shortly. "
                    f"Is there anything else I can help you with?"
                )
            else:
                error = result.get("error", "Unknown error")
                logger.error(f"❌ Booking failed: {error}")
                
                if "conflict" in error.lower() or "booked" in error.lower():
                    return (
                        f"I'm sorry, but {parsed.get('user_friendly')} just became unavailable. "
                        f"Let me suggest alternative times: tomorrow at 10 AM, 2 PM, or 4 PM. Which works better?"
                    )
                else:
                    return "I'm having trouble completing that booking. Let me check other available times for you."
                        
        elif function_name == "get_ticket_status":
            ticket_id = arguments.get("ticket_id")
            
            if not ticket_id:
                return "I need a ticket ID to check the status. It usually looks like TKT-123456."
            
            result = await ticket_service.get_ticket(
                company_id=company_id,
                ticket_id=ticket_id
            )
            
            if result.get("success"):
                ticket = result.get("ticket", {})
                status = ticket.get("status", "unknown")
                priority = ticket.get("priority", "medium")
                assigned = ticket.get("assigned_to", "not assigned yet")
                
                return (
                    f"Your ticket {ticket_id} is currently {status} with {priority} priority. "
                    f"It's {assigned}. Is there anything specific you'd like to know about this ticket?"
                )
            else:
                return f"I couldn't find a ticket with ID {ticket_id}. Could you please verify the ticket number?"
        
        else:
            logger.warning(f"Unknown function: {function_name}")
            return "I'm not sure how to help with that. Let me connect you with a human agent."
            
    except Exception as e:
        logger.error(f"❌ Function execution error in {function_name}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return "I encountered an error processing your request. Let me connect you with support."
