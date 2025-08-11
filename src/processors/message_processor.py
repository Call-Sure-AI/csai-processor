"""
Message Processor Implementation
"""
import time
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from core.interfaces import IMessageProcessor, IConversationManager, IAgentManager, IRAGService, ILogger


class MessageProcessor(IMessageProcessor):
    """
    Processes incoming messages and generates responses using conversation and agent managers
    """
    
    def __init__(self, logger: Optional[ILogger] = None):
        self.logger = logger or logging.getLogger(__name__)

    async def process_message(
        self,
        client_id: str,
        message_data: dict,
        conversation_manager: IConversationManager,
        agent_manager: IAgentManager,
        rag_service: IRAGService
    ) -> str:
        """Process message and return response"""
        start_time = time.time()
        
        try:
            # Extract message content
            message = message_data.get('message', '')
            company_id = message_data.get('company_id')
            agent_id = message_data.get('agent_id')
            
            if not message or not company_id:
                raise ValueError("Missing required fields: message or company_id")
            
            # Get or create conversation
            conversation, is_new = await conversation_manager.get_or_create_conversation(
                customer_id=client_id,
                company_id=company_id,
                agent_id=agent_id
            )
            
            # Get conversation context
            context = await conversation_manager.get_conversation_context(
                conversation_id=conversation.id,
                max_history=5,
                include_system_prompt=True
            )
            
            # Find best agent if not specified
            if not agent_id:
                agent_id, confidence = await agent_manager.find_best_agent(
                    company_id=company_id,
                    query=message,
                    current_agent_id=conversation.current_agent_id
                )
                if not agent_id:
                    raise ValueError("No suitable agent found")
            
            # Get agent information
            agent_info = await agent_manager.get_agent_by_id(agent_id)
            if not agent_info:
                raise ValueError(f"Agent {agent_id} not found")
            
            # Create RAG chain
            chain = await rag_service.create_qa_chain(
                company_id=company_id,
                agent_id=agent_id,
                agent_prompt=agent_info['prompt']
            )
            
            # Generate response using RAG
            response_tokens = []
            async for token in rag_service.get_answer_with_chain(
                chain=chain,
                question=message,
                conversation_context=context
            ):
                response_tokens.append(token)
            
            response = ''.join(response_tokens)
            
            # Update conversation with interaction
            await conversation_manager.update_conversation(
                conversation_id=conversation.id,
                user_message=message,
                ai_response=response,
                agent_id=agent_id,
                metadata={
                    "processing_time": time.time() - start_time,
                    "tokens_generated": len(response_tokens),
                    "confidence": 0.8  # Default confidence
                }
            )
            
            # Update agent performance metrics
            await agent_manager.update_agent_performance(
                agent_id=agent_id,
                confidence_score=0.8,
                response_time=time.time() - start_time,
                was_successful=True
            )
            
            self.logger.info(f"Processed message for client {client_id} in {time.time() - start_time:.3f}s")
            return response
            
        except Exception as e:
            self.logger.error(f"Error processing message: {str(e)}")
            raise


class StreamingMessageProcessor(IMessageProcessor):
    """
    Processes messages with streaming response support
    """
    
    def __init__(self, logger: Optional[ILogger] = None):
        self.logger = logger or logging.getLogger(__name__)

    async def process_message(
        self,
        client_id: str,
        message_data: dict,
        conversation_manager: IConversationManager,
        agent_manager: IAgentManager,
        rag_service: IRAGService
    ) -> str:
        """Process message with streaming support"""
        start_time = time.time()
        
        try:
            # Extract message content
            message = message_data.get('message', '')
            company_id = message_data.get('company_id')
            agent_id = message_data.get('agent_id')
            
            if not message or not company_id:
                raise ValueError("Missing required fields: message or company_id")
            
            # Get or create conversation
            conversation, is_new = await conversation_manager.get_or_create_conversation(
                customer_id=client_id,
                company_id=company_id,
                agent_id=agent_id
            )
            
            # Get conversation context
            context = await conversation_manager.get_conversation_context(
                conversation_id=conversation.id,
                max_history=5,
                include_system_prompt=True
            )
            
            # Find best agent if not specified
            if not agent_id:
                agent_id, confidence = await agent_manager.find_best_agent(
                    company_id=company_id,
                    query=message,
                    current_agent_id=conversation.current_agent_id
                )
                if not agent_id:
                    raise ValueError("No suitable agent found")
            
            # Get agent information
            agent_info = await agent_manager.get_agent_by_id(agent_id)
            if not agent_info:
                raise ValueError(f"Agent {agent_id} not found")
            
            # Create RAG chain
            chain = await rag_service.create_qa_chain(
                company_id=company_id,
                agent_id=agent_id,
                agent_prompt=agent_info['prompt']
            )
            
            # Generate streaming response
            response_tokens = []
            async for token in rag_service.get_answer_with_chain(
                chain=chain,
                question=message,
                conversation_context=context
            ):
                response_tokens.append(token)
                # In a real implementation, you would yield each token here
                # For now, we collect them and return the full response
            
            response = ''.join(response_tokens)
            
            # Update conversation with interaction
            await conversation_manager.update_conversation(
                conversation_id=conversation.id,
                user_message=message,
                ai_response=response,
                agent_id=agent_id,
                metadata={
                    "processing_time": time.time() - start_time,
                    "tokens_generated": len(response_tokens),
                    "confidence": 0.8,
                    "streaming": True
                }
            )
            
            # Update agent performance metrics
            await agent_manager.update_agent_performance(
                agent_id=agent_id,
                confidence_score=0.8,
                response_time=time.time() - start_time,
                was_successful=True
            )
            
            self.logger.info(f"Processed streaming message for client {client_id} in {time.time() - start_time:.3f}s")
            return response
            
        except Exception as e:
            self.logger.error(f"Error processing streaming message: {str(e)}")
            raise

    async def process_message_stream(
        self,
        client_id: str,
        message_data: dict,
        conversation_manager: IConversationManager,
        agent_manager: IAgentManager,
        rag_service: IRAGService
    ):
        """Process message and yield streaming response tokens"""
        start_time = time.time()
        
        try:
            # Extract message content
            message = message_data.get('message', '')
            company_id = message_data.get('company_id')
            agent_id = message_data.get('agent_id')
            
            if not message or not company_id:
                raise ValueError("Missing required fields: message or company_id")
            
            # Get or create conversation
            conversation, is_new = await conversation_manager.get_or_create_conversation(
                customer_id=client_id,
                company_id=company_id,
                agent_id=agent_id
            )
            
            # Get conversation context
            context = await conversation_manager.get_conversation_context(
                conversation_id=conversation.id,
                max_history=5,
                include_system_prompt=True
            )
            
            # Find best agent if not specified
            if not agent_id:
                agent_id, confidence = await agent_manager.find_best_agent(
                    company_id=company_id,
                    query=message,
                    current_agent_id=conversation.current_agent_id
                )
                if not agent_id:
                    raise ValueError("No suitable agent found")
            
            # Get agent information
            agent_info = await agent_manager.get_agent_by_id(agent_id)
            if not agent_info:
                raise ValueError(f"Agent {agent_id} not found")
            
            # Create RAG chain
            chain = await rag_service.create_qa_chain(
                company_id=company_id,
                agent_id=agent_id,
                agent_prompt=agent_info['prompt']
            )
            
            # Generate streaming response
            response_tokens = []
            async for token in rag_service.get_answer_with_chain(
                chain=chain,
                question=message,
                conversation_context=context
            ):
                response_tokens.append(token)
                yield token
            
            response = ''.join(response_tokens)
            
            # Update conversation with interaction
            await conversation_manager.update_conversation(
                conversation_id=conversation.id,
                user_message=message,
                ai_response=response,
                agent_id=agent_id,
                metadata={
                    "processing_time": time.time() - start_time,
                    "tokens_generated": len(response_tokens),
                    "confidence": 0.8,
                    "streaming": True
                }
            )
            
            # Update agent performance metrics
            await agent_manager.update_agent_performance(
                agent_id=agent_id,
                confidence_score=0.8,
                response_time=time.time() - start_time,
                was_successful=True
            )
            
            self.logger.info(f"Processed streaming message for client {client_id} in {time.time() - start_time:.3f}s")
            
        except Exception as e:
            self.logger.error(f"Error processing streaming message: {str(e)}")
            raise
