from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from database.models import Ticket, TicketNote, Conversation, Company, TicketStatus, TicketPriority, TicketSource
from datetime import datetime, timedelta
import logging
import re
import uuid

logger = logging.getLogger(__name__)

class AutoTicketService:
    def __init__(self, db_session: Session):
        self.db = db_session
        
        # Keywords that trigger ticket creation
        self.issue_keywords = [
            "problem", "issue", "error", "bug", "broken", "not working", 
            "can't", "cannot", "unable", "help", "support", "complaint",
            "refund", "billing", "charge", "payment", "account", "access"
        ]
        
        self.urgency_keywords = {
            "critical": ["urgent", "emergency", "critical", "asap", "immediately"],
            "high": ["important", "soon", "quickly", "priority"],
            "medium": ["when possible", "sometime", "eventually"],
            "low": ["minor", "small", "tiny", "cosmetic"]
        }

    async def analyze_conversation_for_tickets(
        self, 
        conversation_id: str, 
        latest_messages: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Analyze conversation messages to determine if a ticket should be created"""
        
        try:
            # Get conversation details
            conversation = self.db.query(Conversation).filter_by(id=conversation_id).first()
            if not conversation:
                return None

            # Check if ticket already exists for this conversation
            existing_ticket = self.db.query(Ticket).filter_by(conversation_id=conversation_id).first()
            if existing_ticket:
                # Update existing ticket if needed
                return await self._update_existing_ticket(existing_ticket, latest_messages)

            # Analyze messages for issue indicators
            combined_text = " ".join([msg.get("content", "") for msg in latest_messages]).lower()
            
            # Check for issue keywords
            issue_detected = any(keyword in combined_text for keyword in self.issue_keywords)
            
            if not issue_detected:
                return None

            # Determine priority based on urgency keywords
            priority = self._determine_priority(combined_text)
            
            # Generate ticket title and description
            title = self._generate_ticket_title(latest_messages)
            description = self._generate_ticket_description(latest_messages, conversation)
            
            # Create the ticket
            ticket_data = {
                "company_id": conversation.company_id,
                "conversation_id": conversation_id,
                "customer_id": conversation.customer_id,
                "title": title,
                "description": description,
                "priority": priority,
                "source": TicketSource.AUTO_GENERATED,
                "customer_name": self._extract_customer_name(conversation),
                "metadata": {
                    "auto_generated": True,
                    "trigger_keywords": [kw for kw in self.issue_keywords if kw in combined_text],
                    "conversation_length": len(conversation.history or []),
                    "analysis_timestamp": datetime.utcnow().isoformat()
                }
            }
            
            return ticket_data

        except Exception as e:
            logger.error(f"Error analyzing conversation for tickets: {str(e)}")
            return None

    def _determine_priority(self, text: str) -> TicketPriority:
        """Determine ticket priority based on text analysis"""
        for priority, keywords in self.urgency_keywords.items():
            if any(keyword in text for keyword in keywords):
                return TicketPriority(priority)
        return TicketPriority.MEDIUM

    def _generate_ticket_title(self, messages: List[Dict[str, Any]]) -> str:
        """Generate a descriptive title for the ticket"""
        if not messages:
            return "Customer Support Request"
        
        # Use the first user message or a summary
        first_user_msg = next((msg for msg in messages if msg.get("role") == "user"), {})
        content = first_user_msg.get("content", "")
        
        # Truncate and clean up
        title = content[:100].strip()
        if len(content) > 100:
            title += "..."
            
        return title or "Customer Support Request"

    def _generate_ticket_description(self, messages: List[Dict[str, Any]], conversation: Conversation) -> str:
        """Generate a detailed description for the ticket"""
        description_parts = [
            "**Auto-generated ticket from conversation**",
            f"**Conversation ID:** {conversation.id}",
            f"**Customer:** {conversation.customer_id}",
            "",
            "**Recent Messages:**"
        ]
        
        for msg in messages[-5:]:  # Last 5 messages
            role = msg.get("role", "unknown").title()
            content = msg.get("content", "")[:200]  # Truncate long messages
            timestamp = msg.get("timestamp", "")
            description_parts.append(f"**{role}** ({timestamp}): {content}")
        
        return "\n".join(description_parts)

    def _extract_customer_name(self, conversation: Conversation) -> Optional[str]:
        """Extract customer name from conversation metadata"""
        metadata = conversation.meta_data or {}
        return metadata.get("customer_name") or metadata.get("client_info", {}).get("name")

    async def create_ticket(self, ticket_data: Dict[str, Any]) -> Ticket:
        """Create a new ticket in the database"""
        try:
            ticket = Ticket(**ticket_data)
            self.db.add(ticket)
            self.db.commit()
            self.db.refresh(ticket)
            
            # Add initial note
            initial_note = TicketNote(
                ticket_id=ticket.id,
                content="Ticket automatically created based on conversation analysis.",
                author="System",
                is_internal=True
            )
            self.db.add(initial_note)
            self.db.commit()
            
            logger.info(f"Created automatic ticket {ticket.id} for conversation {ticket.conversation_id}")
            return ticket
            
        except Exception as e:
            logger.error(f"Error creating ticket: {str(e)}")
            self.db.rollback()
            raise

    async def get_tickets_for_company(
        self, 
        company_id: str, 
        status_filter: Optional[List[str]] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Get tickets for a company with filtering and pagination"""
        
        try:
            query = self.db.query(Ticket).filter_by(company_id=company_id)
            
            if status_filter:
                query = query.filter(Ticket.status.in_(status_filter))
            
            # Get total count
            total_count = query.count()
            
            # Apply pagination and get results
            tickets = query.order_by(Ticket.created_at.desc()).offset(offset).limit(limit).all()
            
            # Convert to dict format
            ticket_list = []
            for ticket in tickets:
                ticket_dict = {
                    "id": ticket.id,
                    "title": ticket.title,
                    "status": ticket.status.value,
                    "priority": ticket.priority.value,
                    "source": ticket.source.value,
                    "customer_name": ticket.customer_name,
                    "customer_email": ticket.customer_email,
                    "assigned_to": ticket.assigned_to,
                    "created_at": ticket.created_at.isoformat(),
                    "updated_at": ticket.updated_at.isoformat(),
                    "conversation_id": ticket.conversation_id
                }
                ticket_list.append(ticket_dict)
            
            return ticket_list, total_count
            
        except Exception as e:
            logger.error(f"Error getting tickets: {str(e)}")
            return [], 0

    async def get_ticket_details(self, ticket_id: str, company_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed ticket information including conversation history"""
        
        try:
            ticket = self.db.query(Ticket).filter_by(
                id=ticket_id, 
                company_id=company_id
            ).first()
            
            if not ticket:
                return None
            
            # Get conversation history if available
            conversation_history = []
            if ticket.conversation_id:
                conversation = self.db.query(Conversation).filter_by(
                    id=ticket.conversation_id
                ).first()
                
                if conversation and conversation.history:
                    conversation_history = conversation.history[-10:]  # Last 10 messages
            
            # Get ticket notes
            notes = self.db.query(TicketNote).filter_by(
                ticket_id=ticket_id
            ).order_by(TicketNote.created_at.asc()).all()
            
            ticket_details = {
                "id": ticket.id,
                "title": ticket.title,
                "description": ticket.description,
                "status": ticket.status.value,
                "priority": ticket.priority.value,
                "source": ticket.source.value,
                "customer_id": ticket.customer_id,
                "customer_name": ticket.customer_name,
                "customer_email": ticket.customer_email,
                "customer_phone": ticket.customer_phone,
                "assigned_to": ticket.assigned_to,
                "tags": ticket.tags,
                "metadata": ticket.metadata,
                "created_at": ticket.created_at.isoformat(),
                "updated_at": ticket.updated_at.isoformat(),
                "conversation_history": conversation_history,
                "notes": [
                    {
                        "id": note.id,
                        "content": note.content,
                        "author": note.author,
                        "is_internal": note.is_internal,
                        "created_at": note.created_at.isoformat()
                    }
                    for note in notes
                ]
            }
            
            return ticket_details
            
        except Exception as e:
            logger.error(f"Error getting ticket details: {str(e)}")
            return None

    async def update_ticket_status(
        self, 
        ticket_id: str, 
        new_status: str, 
        company_id: str,
        updated_by: str,
        note: Optional[str] = None
    ) -> bool:
        """Update ticket status with optional note"""
        
        try:
            ticket = self.db.query(Ticket).filter_by(
                id=ticket_id, 
                company_id=company_id
            ).first()
            
            if not ticket:
                return False
            
            old_status = ticket.status.value
            ticket.status = TicketStatus(new_status)
            ticket.updated_at = datetime.utcnow()
            
            # Set resolved/closed timestamps
            if new_status == "resolved" and not ticket.resolved_at:
                ticket.resolved_at = datetime.utcnow()
            elif new_status == "closed" and not ticket.closed_at:
                ticket.closed_at = datetime.utcnow()
            
            # Add status change note
            status_note = TicketNote(
                ticket_id=ticket_id,
                content=f"Status changed from {old_status} to {new_status}" + (f"\n\nNote: {note}" if note else ""),
                author=updated_by,
                is_internal=True
            )
            self.db.add(status_note)
            
            self.db.commit()
            logger.info(f"Updated ticket {ticket_id} status from {old_status} to {new_status}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating ticket status: {str(e)}")
            self.db.rollback()
            return False

    async def add_ticket_note(
        self, 
        ticket_id: str, 
        content: str, 
        author: str, 
        company_id: str,
        is_internal: bool = True
    ) -> bool:
        """Add a note to a ticket"""
        
        try:
            # Verify ticket exists and belongs to company
            ticket = self.db.query(Ticket).filter_by(
                id=ticket_id, 
                company_id=company_id
            ).first()
            
            if not ticket:
                return False
            
            note = TicketNote(
                ticket_id=ticket_id,
                content=content,
                author=author,
                is_internal=is_internal
            )
            
            self.db.add(note)
            ticket.updated_at = datetime.utcnow()
            self.db.commit()
            
            logger.info(f"Added note to ticket {ticket_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error adding ticket note: {str(e)}")
            self.db.rollback()
            return False