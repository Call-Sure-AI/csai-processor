from typing import Dict, Any, List
from services.ticket_service import ticket_service
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
    call_sid: str
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
