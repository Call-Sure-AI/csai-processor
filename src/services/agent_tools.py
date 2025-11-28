from typing import Dict, Any, List
from services.ticket_service import ticket_service
from services.booking_service import booking_service
from datetime import datetime
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
    """Parse time slot from natural language"""
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d")
        
        time_str = preferred_time.upper().replace(" ", "")
        if "AM" in time_str or "PM" in time_str:
            hour = int(time_str.split(":")[0])
            if "PM" in time_str and hour != 12:
                hour += 12
            elif "AM" in time_str and hour == 12:
                hour = 0
        else:
            hour = int(preferred_time.split(":")[0])
        
        slot_start = date.replace(hour=hour, minute=0, second=0)
        slot_end = slot_start + timedelta(minutes=30)
        
        return slot_start.isoformat(), slot_end.isoformat()
        
    except Exception as e:
        logger.error(f"Error parsing time: {str(e)}")
        tomorrow = datetime.now() + timedelta(days=1)
        slot_start = tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)
        slot_end = slot_start + timedelta(minutes=30)
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
