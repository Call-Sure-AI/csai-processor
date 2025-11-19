# src\managers\conversation_manager.py
"""
Conversation Manager Implementation
"""
from typing import List, Dict, Optional, Tuple, Any
from sqlalchemy.orm import Session
from sqlalchemy import update
import datetime
import logging
import uuid
import asyncio
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from database.models import Conversation, Company
from core.interfaces import IConversationManager, IBackgroundWorker, ILogger
from config.settings import settings


class ConversationManager(IConversationManager):
    """
    Manages conversation lifecycle and caching with proper separation of concerns
    """
    
    def __init__(
        self,
        db_session: Session,
        background_worker: Optional[IBackgroundWorker] = None,
        logger: Optional[ILogger] = None
    ):
        self.db = db_session
        self.bg_worker = background_worker
        self.logger = logger or logging.getLogger(__name__)
        
        # Cache management
        self.cache = {}
        self.company_cache = {}
        self.cache_ttl = timedelta(seconds=settings.conversation_cache_ttl)
        self.last_cache_cleanup = datetime.utcnow()
        
        # Thread safety
        self._cache_lock = asyncio.Lock()
        self._cleanup_lock = asyncio.Lock()

    def set_background_worker(self, background_worker: IBackgroundWorker):
        """Set background worker after initialization"""
        self.bg_worker = background_worker

    async def get_or_create_conversation(
        self,
        customer_id: str,
        company_id: str,
        agent_id: Optional[str] = None
    ) -> Tuple[Conversation, bool]:
        """Get existing conversation or create new one with proper error handling"""
        try:
            async with self._cache_lock:
                # Check cache first
                cache_key = f"{company_id}:{customer_id}"
                if cache_key in self.cache:
                    return self.cache[cache_key], False

                # Query database
                conversation = self.db.query(Conversation).filter_by(
                    customer_id=customer_id,
                    company_id=company_id
                ).order_by(Conversation.created_at.desc()).first()

                if conversation:
                    if conversation.history is None:
                        conversation.history = []
                        self.db.commit()
                    self.cache[cache_key] = conversation
                    return conversation, False

                # Create new conversation
                conversation = await self.create_conversation(
                    customer_id, company_id, agent_id
                )
                self.cache[cache_key] = conversation
                return conversation, True

        except Exception as e:
            self.logger.error(f"Error in get_or_create_conversation: {str(e)}")
            raise

    async def create_conversation(
        self,
        customer_id: str,
        company_id: str,
        agent_id: Optional[str] = None
    ) -> Conversation:
        """Create a new conversation with metadata"""
        try:
            conversation_id = str(uuid.uuid4())
            conversation = Conversation(
                id=conversation_id,
                customer_id=customer_id,
                company_id=company_id,
                current_agent_id=agent_id,
                history=[],
                meta_data={
                    "started_at": datetime.utcnow().isoformat(),
                    "source": "websocket",
                    "initial_agent": agent_id
                }
            )
            self.db.add(conversation)
            self.db.commit()
            
            self.logger.info(f"Created new conversation {conversation_id} for customer {customer_id}")
            return conversation

        except Exception as e:
            self.logger.error(f"Error creating conversation: {str(e)}")
            self.db.rollback()
            raise

    async def update_conversation(
        self,
        conversation_id: str,
        user_message: str,
        ai_response: str,
        agent_id: str,
        metadata: Optional[Dict] = None
    ) -> None:
        """Update conversation with new messages and metadata"""
        try:
            async with self._cache_lock:
                # Update cache
                if conversation_id not in self.cache:
                    conversation = self.db.query(Conversation).filter_by(
                        id=conversation_id
                    ).first()
                    if not conversation:
                        raise ValueError(f"Conversation {conversation_id} not found")
                    self.cache[conversation_id] = conversation

                current_history = self.cache[conversation_id].history or []
                
                # Create new message entry
                new_message = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "agent_id": agent_id,
                    "user_message": user_message,
                    "ai_response": ai_response,
                    "metadata": metadata or {}
                }

                new_history = current_history + [new_message]
                self.cache[conversation_id].history = new_history

                # Queue background update if worker exists
                if self.bg_worker:
                    self.bg_worker.enqueue(
                        self._update_history_in_db,
                        conversation_id,
                        new_history,
                        agent_id,
                        metadata
                    )
                else:
                    # Immediate update if no background worker
                    await self._update_history_in_db(
                        self.db,
                        conversation_id,
                        new_history,
                        agent_id,
                        metadata
                    )

                self.logger.info(f"Conversation {conversation_id} updated")

        except Exception as e:
            self.logger.error(f"Error updating conversation: {str(e)}")
            raise

    @staticmethod
    async def _update_history_in_db(
        db: Session,
        conversation_id: str,
        history: List[Dict],
        agent_id: str,
        metadata: Optional[Dict] = None
    ):
        """Update conversation history in database"""
        try:
            stmt = update(Conversation).where(
                Conversation.id == conversation_id
            ).values(
                history=history,
                current_agent_id=agent_id,
                meta_data=metadata if metadata else Conversation.meta_data,
                updated_at=datetime.utcnow()
            )
            db.execute(stmt)
            db.commit()
        except Exception as e:
            logging.error(f"Error in database update: {str(e)}")
            db.rollback()
            raise

    async def get_conversation_context(
        self,
        conversation_id: str,
        max_history: int = 5,
        include_system_prompt: bool = True
    ) -> List[Dict]:
        """Get conversation context with configurable history size"""
        try:
            async with self._cache_lock:
                # Get conversation history
                if conversation_id in self.cache:
                    conversation = self.cache[conversation_id]
                else:
                    conversation = self.db.query(Conversation).filter_by(
                        id=conversation_id
                    ).first()
                    if not conversation:
                        raise ValueError(f"Conversation {conversation_id} not found")
                    self.cache[conversation_id] = conversation

                # Initialize context
                chat_messages = []
                if include_system_prompt:
                    system_msg = await self._get_core_system_prompt(conversation.company_id)
                    chat_messages.append({
                        "role": "system",
                        "content": system_msg
                    })

                # Add recent history
                history = conversation.history or []
                recent_history = history[-max_history:] if history else []
                
                for msg in recent_history:
                    if msg.get("user_message"):
                        chat_messages.append({
                            "role": "user",
                            "content": msg["user_message"]
                        })
                    if msg.get("ai_response"):
                        chat_messages.append({
                            "role": "assistant",
                            "content": msg["ai_response"]
                        })

                return chat_messages

        except Exception as e:
            self.logger.error(f"Error getting conversation context: {str(e)}")
            raise

    async def _get_core_system_prompt(self, company_id: str) -> str:
        """Get customized system prompt for company"""
        try:
            async with self._cache_lock:
                # Get company from cache or database
                if company_id in self.company_cache:
                    company = self.company_cache[company_id]
                else:
                    company = self.db.query(Company).filter_by(id=company_id).first()
                    if company:
                        self.company_cache[company_id] = company

                base_prompt = (
                    "You are an AI assistant specialized in providing concise, "
                    "accurate, and helpful responses. "
                )

                if company:
                    base_prompt += f"Representing {company.name}. "
                
                base_prompt += (
                    "Please provide concise and relevant responses. "
                    "Always be helpful and professional."
                )

                return base_prompt

        except Exception as e:
            self.logger.error(f"Error generating system prompt: {str(e)}")
            return "You are a helpful AI assistant. Please provide concise and relevant responses."

    async def cleanup_cache(self, force: bool = False):
        """Clean up expired cache entries with thread safety"""
        try:
            async with self._cleanup_lock:
                current_time = datetime.utcnow()
                if not force and (current_time - self.last_cache_cleanup) < timedelta(minutes=5):
                    return

                async with self._cache_lock:
                    expired_keys = [
                        key for key, conversation in self.cache.items()
                        if current_time - conversation.updated_at > self.cache_ttl
                    ]

                    for key in expired_keys:
                        self.cache.pop(key, None)

                    # Clean up company cache as well
                    expired_company_keys = [
                        key for key, company in self.company_cache.items()
                        if current_time - company.updated_at > self.cache_ttl
                    ]

                    for key in expired_company_keys:
                        self.company_cache.pop(key, None)

                self.last_cache_cleanup = current_time
                self.logger.info(f"Cleaned up {len(expired_keys)} conversation cache entries and {len(expired_company_keys)} company cache entries")
                
        except Exception as e:
            self.logger.error(f"Error in cache cleanup: {str(e)}")

    @asynccontextmanager
    async def get_conversation_session(self, conversation_id: str):
        """Context manager for conversation operations"""
        try:
            conversation = await self._get_conversation(conversation_id)
            yield conversation
        except Exception as e:
            self.logger.error(f"Error in conversation session: {str(e)}")
            raise
        finally:
            # Any cleanup if needed
            pass

    async def _get_conversation(self, conversation_id: str) -> Conversation:
        """Get conversation with caching"""
        async with self._cache_lock:
            if conversation_id in self.cache:
                return self.cache[conversation_id]
            
            conversation = self.db.query(Conversation).filter_by(id=conversation_id).first()
            if not conversation:
                raise ValueError(f"Conversation {conversation_id} not found")
            
            self.cache[conversation_id] = conversation
            return conversation

    async def end_conversation(self, conversation_id: str, ended_by: str = "user") -> bool:
        """End a conversation"""
        try:
            async with self._cache_lock:
                conversation = await self._get_conversation(conversation_id)
                conversation.status = "ended"
                conversation.ended_by = ended_by
                conversation.ended_at = datetime.utcnow()
                
                # Update cache
                self.cache[conversation_id] = conversation
                
                # Queue database update
                if self.bg_worker:
                    self.bg_worker.enqueue(
                        self._end_conversation_in_db,
                        conversation_id,
                        ended_by
                    )
                else:
                    await self._end_conversation_in_db(self.db, conversation_id, ended_by)
                
                self.logger.info(f"Conversation {conversation_id} ended by {ended_by}")
                return True
                
        except Exception as e:
            self.logger.error(f"Error ending conversation: {str(e)}")
            return False

    @staticmethod
    async def _end_conversation_in_db(db: Session, conversation_id: str, ended_by: str):
        """End conversation in database"""
        try:
            stmt = update(Conversation).where(
                Conversation.id == conversation_id
            ).values(
                status="ended",
                ended_by=ended_by,
                ended_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.execute(stmt)
            db.commit()
        except Exception as e:
            logging.error(f"Error ending conversation in database: {str(e)}")
            db.rollback()
            raise
