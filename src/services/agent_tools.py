from typing import Dict, Any, List
from services.ticket_service import ticket_service
from services.booking_service import booking_service
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
        "description": "Check if a time slot is available for booking. Use this BEFORE creating a booking to verify availability.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_phone": {"type": "string", "description": "Customer's phone number"},
                "preferred_date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                "preferred_time": {"type": "string", "description": "Time like '10:00 AM' or '2:00 PM'"},
                "campaign_id": {"type": "string", "description": "Campaign ID"}
            },
            "required": ["customer_phone", "preferred_date", "preferred_time", "campaign_id"]
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
        "description": "Schedule an appointment when customer agrees to book a time slot.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "Customer's full name"},
                "customer_phone": {"type": "string", "description": "Customer's phone number"},
                "preferred_date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                "preferred_time": {"type": "string", "description": "Time like '10:00 AM' or '2:00 PM'"},
                "customer_email": {"type": "string", "description": "Customer's email"},
                "notes": {"type": "string", "description": "Special notes"}
            },
            "required": ["customer_name", "customer_phone", "preferred_date", "preferred_time"]
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

def parse_time_slot(preferred_time: str, date_str: str) -> tuple:
    """Parse time slot from natural language with proper date handling"""
    try:
        today = datetime.now().date()

        try:
            date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except:
            try:
                date = datetime.strptime(date_str, "%d-%m-%Y").date()
            except:
                logger.warning(f"Invalid date format: {date_str}, using tomorrow")
                date = today + timedelta(days=1)

        if date < today:
            logger.warning(f"Date {date_str} is in the past, using tomorrow instead")
            date = today + timedelta(days=1)

        max_future_date = today + timedelta(days=90)
        if date > max_future_date:
            logger.warning(f"Date {date_str} is too far in future, using tomorrow")
            date = today + timedelta(days=1)
        
        # Parse time
        time_str = preferred_time.upper().replace(" ", "")
        
        if "AM" in time_str or "PM" in time_str:
            time_parts = time_str.replace("AM", "").replace("PM", "").split(":")
            hour = int(time_parts[0])
            minute = int(time_parts[1]) if len(time_parts) > 1 else 0
            
            if "PM" in time_str and hour != 12:
                hour += 12
            elif "AM" in time_str and hour == 12:
                hour = 0
        else:
            time_parts = preferred_time.split(":")
            hour = int(time_parts[0])
            minute = int(time_parts[1]) if len(time_parts) > 1 else 0

        if hour < 9 or hour >= 18:
            logger.warning(f"Time {preferred_time} is outside business hours, using 10 AM")
            hour = 10
            minute = 0
        
        # Create datetime
        slot_start = datetime.combine(date, datetime.min.time()).replace(hour=hour, minute=minute, second=0, microsecond=0)
        slot_end = slot_start + timedelta(minutes=30)
        
        logger.info(f"Parsed slot: {slot_start.isoformat()} to {slot_end.isoformat()}")
        
        return slot_start.isoformat(), slot_end.isoformat()
        
    except Exception as e:
        logger.error(f"Error parsing time slot: {str(e)}")

        tomorrow = datetime.now() + timedelta(days=1)
        slot_start = tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)
        slot_end = slot_start + timedelta(minutes=30)
        
        logger.info(f"Using fallback: {slot_start.isoformat()}")
        
        return slot_start.isoformat(), slot_end.isoformat()

async def execute_function(
    function_name: str,
    arguments: Dict[str, Any],
    company_id: str,
    call_sid: str,
    campaign_id: str = None
) -> str:
    """Execute agent function and return response"""
    try:
        logger.info(f"Executing function: {function_name}")
        
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
            customer_phone = arguments.get("customer_phone")
            preferred_date = arguments.get("preferred_date")
            preferred_time = arguments.get("preferred_time")
            campaign_id_arg = arguments.get("campaign_id", campaign_id)
            
            # Parse time slot
            slot_start, slot_end = parse_time_slot(preferred_time, preferred_date)
            
            # Check availability
            availability = await booking_service.check_slot_availability(
                campaign_id=campaign_id_arg,
                slot_start=slot_start,
                slot_end=slot_end,
                customer_phone=customer_phone
            )
            
            if availability['available']:
                return (
                    f"Great news! {preferred_date} at {preferred_time} is available. "
                    f"To proceed, I'll need your email address for the confirmation. "
                    f"What's your email?"
                )
            else:
                if availability['customer_already_booked']:
                    existing = availability['existing_booking']
                    return (
                        f"I see you already have a booking on {preferred_date} at {preferred_time}. "
                        f"Your booking ID is {existing.get('id')}. "
                        f"Would you like to choose a different time?"
                    )
                else:
                    return (
                        f"I'm sorry, but {preferred_date} at {preferred_time} is fully booked. "
                        f"Would you like me to suggest alternative times?"
                    )
        
        elif function_name == "verify_customer_email":
            customer_email = arguments.get("customer_email")
            
            # Spell out email
            spelled_email = booking_service.spell_out_email(customer_email)
            
            return (
                f"Let me confirm your email address. I have: {customer_email}. "
                f"That's {spelled_email}. Is that correct?"
            )
            
        elif function_name == "create_booking":
            customer_name = arguments.get("customer_name")
            customer_phone = arguments.get("customer_phone")
            preferred_date = arguments.get("preferred_date")
            preferred_time = arguments.get("preferred_time")
            customer_email = arguments.get("customer_email")
            notes = arguments.get("notes", "")
            
            slot_start, slot_end = parse_time_slot(preferred_time, preferred_date)
            
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
                logger.info(f"Booking created: {booking_id}")
                return f"Excellent choice, {customer_name}! I've scheduled your appointment for {preferred_date} at {preferred_time}. Your booking ID is {booking_id}. You'll receive a confirmation email shortly. Is there anything else I can help you with?"
            else:
                error = result.get("error", "Unknown error")
                logger.error(f"Booking failed: {error}")
                return "I'm having trouble booking that slot. Let me check other available times for you."
                        
        elif function_name == "get_ticket_status":
            ticket_id = arguments.get("ticket_id")
            result = await ticket_service.get_ticket(
                company_id=company_id,
                ticket_id=ticket_id
            )
            
            if result.get("success"):
                ticket = result.get("ticket", {})
                status = ticket.get("status", "unknown")
                priority = ticket.get("priority", "medium")
                assigned = ticket.get("assigned_to", "not assigned yet")
                
                return f"Your ticket {ticket_id} is currently {status} with {priority} priority. It's {assigned}. Is there anything specific you'd like to know about this ticket?"
            else:
                return f"I couldn't find a ticket with ID {ticket_id}. Could you please verify the ticket number?"
        
        else:
            return "I'm not sure how to help with that. Let me connect you with a human agent."
            
    except Exception as e:
        logger.error(f"Function execution error: {str(e)}")
        return "I encountered an error processing your request. Let me connect you with support."
