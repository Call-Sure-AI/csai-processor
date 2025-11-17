from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from enum import Enum
import logging

from services.rag.rag_service import RAGService
from services.vector_store.qdrant_service import QdrantService
from services.speech.sentiment_analyzer_service import SentimentAnalyzer
from services.llm_service import llm_service

logger = logging.getLogger(__name__)

qdrant_service = QdrantService()
rag_service = RAGService(qdrant_service)

class ConversationState(Enum):
    GREETING = "greeting"
    LISTENING = "listening"
    PROCESSING = "processing"
    RESPONDING = "responding"
    CLARIFYING = "clarifying"
    CLOSING = "closing"

class ConversationContext:
    """Maintains conversation context and history"""
    
    def __init__(self, call_sid: str):
        self.call_sid = call_sid
        self.messages: List[Dict] = []
        self.metadata: Dict[str, Any] = {}
        self.state = ConversationState.GREETING
        self.interruption_count = 0
        self.clarification_attempts = 0
        self.sentiment_history = []
        self.function_calls: List[Dict] = []

    def add_function_call(self, function_name: str, arguments: Dict, result: Any):
        """Track function calls made during conversation"""
        self.function_calls.append({
            "function": function_name,
            "arguments": arguments,
            "result": result,
            "timestamp": datetime.utcnow().isoformat()
        })
                
    def add_message(self, role: str, content: str, metadata: Optional[Dict] = None):
        """Add message to conversation history"""
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": metadata or {}
        }
        self.messages.append(message)
        
    def get_context_window(self, max_messages: int = 10) -> List[Dict]:
        """Get recent conversation context for LLM"""
        return self.messages[-max_messages:]
        
    def update_metadata(self, key: str, value: Any):
        """Update conversation metadata"""
        self.metadata[key] = value

class ConversationManager:
    """Manages conversation flow and LLM interactions"""
    
    def __init__(self, agent_config: Dict):
        self.agent_config = agent_config
        self.active_conversations: Dict[str, ConversationContext] = {}
        self.interruption_handler = InterruptionHandler()
        self.sentiment_analyzer = SentimentAnalyzer()
        
    async def start_call(self, call_sid: str) -> str:
        """Initialize a new conversation"""
        context = ConversationContext(call_sid)
        self.active_conversations[call_sid] = context
        
        # Generate greeting using LLM service
        greeting = await llm_service.generate_greeting(self.agent_config)
        context.add_message("assistant", greeting)
        
        return greeting

    async def process_user_input_with_rag(
        self,
        call_sid: str,
        user_input: str,
        company_id: str,
        agent_id: str
    ) -> str:
        """Process user input using RAG"""
        try:
            # Get conversation context
            conversation_context = self.get_conversation_history(call_sid)
            
            # Get answer from RAG
            response_tokens = []
            async for token in rag_service.get_answer(
                company_id=company_id,
                question=user_input,
                agent_id=agent_id,
                conversation_context=conversation_context
            ):
                response_tokens.append(token)
            
            response = "".join(response_tokens)
            
            # Update conversation history
            self.add_to_history(call_sid, "user", user_input)
            self.add_to_history(call_sid, "assistant", response)
            
            return response
            
        except Exception as e:
            logger.error(f"Error processing with RAG: {str(e)}")
            return "I'm having trouble accessing my knowledge base right now."

    async def process_user_input(
        self, 
        call_sid: str, 
        user_input: str,
        is_interruption: bool = False
    ) -> str:
        """Process user input and generate response"""
        context = self.active_conversations.get(call_sid)
        if not context:
            return "I'm sorry, I'm having trouble with our connection."
            
        # Add user message to context
        context.add_message("user", user_input, {
            "is_interruption": is_interruption
        })
        
        # Simple sentiment analysis (avoid complex analyzer for now)
        try:
            sentiment = await self.sentiment_analyzer.analyze(user_input)
            context.sentiment_history.append(sentiment)
        except Exception as e:
            logger.warning(f"Sentiment analysis failed: {str(e)}")
            # Continue without sentiment analysis
        
        # Check if clarification is needed
        if await self._needs_clarification(user_input, context):
            return await self._generate_clarification(context)
            
        # Update conversation state
        context.state = ConversationState.PROCESSING
        
        # Generate LLM response
        response = await self._generate_response(context)
        
        # Add response to context
        context.add_message("assistant", response)
        
        return response
        
    async def _generate_response(self, context: ConversationContext) -> str:
        """
        Generate response using LLM service WITH FUNCTION CALLING SUPPORT
        """
        try:
            # Get conversation messages for LLM
            messages = []
            for msg in context.get_context_window():
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
            
            response = await llm_service.generate_response(
                messages=messages,
                context=context.metadata,
                enable_functions=True,
                call_sid=context.call_sid
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Failed to generate response: {str(e)}")
            return "I'm sorry, I'm having trouble processing your request right now. Could you please try again?"
    
    async def _generate_clarification(self, context: ConversationContext) -> str:
        """Generate clarification question"""
        try:
            clarification_prompt = "The user's input is unclear. Generate a polite clarification question to better understand their needs."
            
            response = await llm_service.generate_response(
                messages=[{"role": "user", "content": clarification_prompt}],
                context=context.metadata
            )
            
            context.clarification_attempts += 1
            return response
            
        except Exception as e:
            logger.error(f"Failed to generate clarification: {str(e)}")
            return "I'm sorry, I didn't quite understand that. Could you please repeat or clarify?"
    
    async def _needs_clarification(self, user_input: str, context: ConversationContext) -> bool:
        """Check if user input needs clarification"""
        # Simple heuristics for now - can be enhanced with more sophisticated analysis
        if len(user_input.strip()) < 3:
            return True
            
        if context.clarification_attempts >= 2:
            return False
            
        # Check for unclear phrases (only very unclear ones)
        unclear_phrases = ["huh", "sorry", "pardon", "um", "uh", "er", "ah"]
        if any(phrase in user_input.lower() for phrase in unclear_phrases):
            return True
            
        # Don't trigger on normal question words like "what", "how", "why", etc.
        # Only trigger on very short or unclear responses
        
        return False
    
    async def end_call(self, call_sid: str) -> str:
        """End the conversation and generate closing message"""
        context = self.active_conversations.get(call_sid)
        if not context:
            return "Thank you for calling. Goodbye!"
            
        try:
            closing_prompt = "Generate a polite closing message for the call. Thank the customer and wish them well."
            
            closing_message = await llm_service.generate_response(
                messages=[{"role": "user", "content": closing_prompt}],
                context=context.metadata
            )
            
            context.add_message("assistant", closing_message)
            context.state = ConversationState.CLOSING
            
            # Clean up
            del self.active_conversations[call_sid]
            
            return closing_message
            
        except Exception as e:
            logger.error(f"Failed to generate closing message: {str(e)}")
            return "Thank you for calling. Have a great day!"

class InterruptionHandler:
    """Handles conversation interruptions"""
    
    def __init__(self):
        self.interruption_threshold = 0.5  # seconds
        
    def detect_interruption(self, audio_duration: float) -> bool:
        """Detect if user interrupted the agent"""
        return audio_duration < self.interruption_threshold
        
    def handle_interruption(self, context: ConversationContext) -> str:
        """Handle conversation interruption"""
        context.interruption_count += 1
        
        if context.interruption_count == 1:
            return "I apologize for interrupting. Please continue."
        elif context.interruption_count == 2:
            return "I'm sorry, I'll let you finish. Please go ahead."
        else:
            return "Please continue with your question."

def create_conversation_manager(agent_config: Dict) -> ConversationManager:
    """Create a conversation manager instance"""
    return ConversationManager(agent_config)