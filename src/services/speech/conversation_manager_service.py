from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from enum import Enum

from services.speech.sentiment_analyzer_service import SentimentAnalyzer

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
    
    def __init__(self, llm_service, agent_config: Dict):
        self.llm_service = llm_service
        self.agent_config = agent_config
        self.active_conversations: Dict[str, ConversationContext] = {}
        self.interruption_handler = InterruptionHandler()
        self.sentiment_analyzer = SentimentAnalyzer()
        
    async def start_call(self, call_sid: str) -> str:
        """Initialize a new conversation"""
        context = ConversationContext(call_sid)
        self.active_conversations[call_sid] = context
        
        # Generate greeting based on agent configuration
        greeting = await self._generate_greeting(context)
        context.add_message("assistant", greeting)
        
        return greeting
        
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
        
        # Analyze sentiment
        sentiment = await self.sentiment_analyzer.analyze(user_input)
        context.sentiment_history.append(sentiment)
        
        # Check if clarification is needed
        if await self._needs_clarification(user_input, context):
            return await self._generate_clarification(context)
            
        # Update conversation state
        context.state = ConversationState.PROCESSING
        
        # Generate LLM response
        response = await self._generate_response(context)
        
        # Add response to context
        context.add_message("assistant", response)
        context.state = ConversationState.RESPONDING
        
        return response
        
    async def _generate_response(self, context: ConversationContext) -> str:
        """Generate response using LLM"""
        # Prepare prompt with context
        system_prompt = self._build_system_prompt(context)
        messages = self._prepare_messages(context)
        
        # Call LLM service
        response = await self.llm_service.generate(
            system_prompt=system_prompt,
            messages=messages,
            temperature=0.7,
            max_tokens=150,
            functions=self._get_available_functions(context)
        )
        
        # Post-process response for speech
        return self._optimize_for_speech(response)
        
    def _build_system_prompt(self, context: ConversationContext) -> str:
        """Build system prompt based on agent configuration and context"""
        base_prompt = self.agent_config.get("system_prompt", "")
        
        # Add context-specific instructions
        if context.sentiment_history and context.sentiment_history[-1] < 0.3:
            base_prompt += "\nThe caller seems frustrated. Be extra helpful and empathetic."
            
        if context.interruption_count > 2:
            base_prompt += "\nThe caller has interrupted multiple times. Keep responses brief."
            
        return base_prompt
        
    def _prepare_messages(self, context: ConversationContext) -> List[Dict]:
        """Prepare message history for LLM"""
        messages = []
        
        # Include relevant context
        for msg in context.get_context_window():
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
            
        return messages
        
    def _optimize_for_speech(self, text: str) -> str:
        """Optimize text for natural speech synthesis"""
        # Remove URLs, email addresses
        import re
        text = re.sub(r'http[s]?://\S+', 'the web link I\'ll send you', text)
        text = re.sub(r'\S+@\S+', 'the email address I\'ll send you', text)
        
        # Expand abbreviations
        abbreviations = {
            "Dr.": "Doctor",
            "Mr.": "Mister",
            "Mrs.": "Missus",
            "vs.": "versus",
            "etc.": "et cetera",
            "i.e.": "that is",
            "e.g.": "for example"
        }
        
        for abbr, full in abbreviations.items():
            text = text.replace(abbr, full)
            
        # Add pauses for natural speech
        text = text.replace(", ", ", <break time='300ms'/> ")
        text = text.replace(". ", ". <break time='500ms'/> ")
        
        return text
        
    async def _needs_clarification(self, user_input: str, context: ConversationContext) -> bool:
        """Determine if clarification is needed"""
        # Check for ambiguous input
        if len(user_input.split()) < 3:
            return True
            
        # Check for low confidence in intent detection
        intent_confidence = await self._detect_intent_confidence(user_input)
        if intent_confidence < 0.6:
            return True
            
        return False
        
    async def _generate_clarification(self, context: ConversationContext) -> str:
        """Generate clarification request"""
        context.clarification_attempts += 1
        
        if context.clarification_attempts > 2:
            return "I'm having trouble understanding. Let me transfer you to a human agent who can better assist you."
            
        clarifications = [
            "I didn't quite catch that. Could you please repeat?",
            "Could you provide more details about what you're looking for?",
            "I want to make sure I understand correctly. Could you rephrase that?"
        ]
        
        return clarifications[min(context.clarification_attempts - 1, len(clarifications) - 1)]

class InterruptionHandler:
    """Handles conversation interruptions gracefully"""
    
    def __init__(self):
        self.interruption_buffer = []
        self.max_interruptions = 3
        
    async def handle_interruption(
        self, 
        current_response: str, 
        interruption_point: float,
        new_input: str
    ) -> Tuple[bool, Optional[str]]:
        """Handle user interruption during agent speech"""
        # Calculate how much was likely heard
        words_spoken = int(interruption_point * 150 / 60)  # Assuming 150 words/minute
        
        # Determine if we should stop and listen
        if "stop" in new_input.lower() or "wait" in new_input.lower():
            return True, "I'll stop. What would you like to know?"
            
        # Store interruption for context
        self.interruption_buffer.append({
            "interrupted_at": words_spoken,
            "new_input": new_input,
            "original_response": current_response
        })
        
        # Continue if brief interruption
        if len(new_input.split()) < 3:
            return False, None
            
        # Stop and address the interruption
        return True, None
    

sentiment_analyzer = SentimentAnalyzer()

conversation_manager = ConversationManager()