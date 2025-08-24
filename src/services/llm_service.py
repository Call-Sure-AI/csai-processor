"""
Multi-Provider LLM Service for Twilio Call Conversations
"""
from typing import Dict, List, Optional, Any
import logging
import asyncio
import json
import httpx
from config.settings import settings

logger = logging.getLogger(__name__)

class MultiProviderLLMService:
    """Service for handling LLM interactions with multiple providers"""
    
    def __init__(self):
        self.claude_api_key = settings.claude_api_key
        self.openai_api_key = getattr(settings, 'openai_api_key', None)
        self.model = getattr(settings, 'openai_model', 'gpt-4')
        self.max_tokens = getattr(settings, 'openai_max_tokens', 2000)
        self.temperature = getattr(settings, 'openai_temperature', 0.7)
        self.provider_priority = ['claude', 'openai', 'fallback']
        
    async def generate_response(
        self, 
        messages: List[Dict[str, str]], 
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate a response from the best available LLM provider"""
        for provider in self.provider_priority:
            try:
                if provider == 'claude' and self.claude_api_key:
                    return await self._generate_claude_response(messages, context)
                elif provider == 'openai' and self.openai_api_key:
                    return await self._generate_openai_response(messages, context)
                elif provider == 'fallback':
                    return await self._generate_fallback_response(messages, context)
            except Exception as e:
                logger.warning(f"Provider {provider} failed: {str(e)}")
                continue
        
        return "I'm sorry, I'm having trouble processing your request right now. Could you please try again?"
    
    async def generate_greeting(self, context: Optional[Dict[str, Any]] = None) -> str:
        """Generate a greeting message"""
        greeting_prompt = "Generate a friendly, professional greeting for a customer service call. Keep it under 50 words."
        
        messages = [
            {"role": "user", "content": greeting_prompt}
        ]
        
        return await self.generate_response(messages, context)
    
    async def analyze_sentiment(self, text: str) -> Dict[str, Any]:
        """Analyze sentiment of user input"""
        try:
            prompt = f"Analyze the sentiment of the following text. Return only a JSON object with 'sentiment' (positive/negative/neutral) and 'confidence' (0-1). Text: {text}"
            
            response = await self.generate_response([
                {"role": "user", "content": prompt}
            ])
            
            # Try to parse JSON from response
            try:
                result = json.loads(response)
                return result
            except json.JSONDecodeError:
                # If not valid JSON, try to extract sentiment from text
                if 'positive' in response.lower():
                    return {"sentiment": "positive", "confidence": 0.7}
                elif 'negative' in response.lower():
                    return {"sentiment": "negative", "confidence": 0.7}
                else:
                    return {"sentiment": "neutral", "confidence": 0.5}
            
        except Exception as e:
            logger.error(f"Failed to analyze sentiment: {str(e)}")
            return {"sentiment": "neutral", "confidence": 0.5}
    
    async def _generate_claude_response(self, messages: List[Dict[str, str]], context: Optional[Dict[str, Any]] = None) -> str:
        """Generate response using Claude API"""
        try:
            system_prompt = self._get_system_prompt(context)
            
            # Prepare messages for Claude
            claude_messages = [{"role": "user", "content": system_prompt}]
            
            # Add conversation messages
            for msg in messages[-10:]:  # Keep last 10 messages
                claude_messages.append(msg)
            
            # Combine all messages into a single prompt
            full_prompt = "\n\n".join([msg["content"] for msg in claude_messages])
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self.claude_api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": "claude-3-sonnet-20240229",
                        "max_tokens": self.max_tokens,
                        "temperature": self.temperature,
                        "messages": [{"role": "user", "content": full_prompt}]
                    },
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    result = response.json()
                    return result["content"][0]["text"].strip()
                else:
                    raise Exception(f"Claude API error: {response.status_code} - {response.text}")
                    
        except Exception as e:
            logger.error(f"Claude API failed: {str(e)}")
            raise
    
    async def _generate_openai_response(self, messages: List[Dict[str, str]], context: Optional[Dict[str, Any]] = None) -> str:
        """Generate response using OpenAI API"""
        try:
            from openai import AsyncOpenAI
            
            client = AsyncOpenAI(api_key=self.openai_api_key)
            
            # Prepare system message
            system_message = {
                "role": "system",
                "content": self._get_system_prompt(context)
            }
            
            # Combine system message with conversation history
            full_messages = [system_message] + messages[-10:]  # Keep last 10 messages for context
            
            response = await client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stream=False
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"OpenAI API failed: {str(e)}")
            raise
    
    async def _generate_fallback_response(self, messages: List[Dict[str, str]], context: Optional[Dict[str, Any]] = None) -> str:
        """Generate fallback response when no LLM is available"""
        try:
            # Get the last user message
            last_user_message = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    last_user_message = msg.get("content", "")
                    break
            
            # Simple rule-based responses
            if not last_user_message:
                return "Hello! How can I help you today?"
            
            last_message_lower = last_user_message.lower()
            
            # Greeting patterns
            if any(word in last_message_lower for word in ['hello', 'hi', 'hey', 'good morning', 'good afternoon']):
                return "Hello! Thank you for calling. How can I assist you today?"
            
            # Question patterns
            elif any(word in last_message_lower for word in ['help', 'support', 'assist', 'problem', 'issue']):
                return "I'd be happy to help you. Could you please tell me more about what you need assistance with?"
            
            # Complaint patterns
            elif any(word in last_message_lower for word in ['complaint', 'angry', 'frustrated', 'unhappy', 'dissatisfied']):
                return "I understand you're having concerns. Let me help you resolve this. Could you please provide more details?"
            
            # Thank you patterns
            elif any(word in last_message_lower for word in ['thank', 'thanks', 'appreciate']):
                return "You're very welcome! Is there anything else I can help you with?"
            
            # Goodbye patterns
            elif any(word in last_message_lower for word in ['goodbye', 'bye', 'end call', 'hang up']):
                return "Thank you for calling. Have a great day!"
            
            # Default response
            else:
                return "I understand. Could you please tell me more about how I can help you?"
                
        except Exception as e:
            logger.error(f"Fallback response failed: {str(e)}")
            return "I'm here to help. How can I assist you today?"
    
    def _get_system_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """Get system prompt based on context"""
        base_prompt = """You are a natural-sounding customer service agent on a live phone call. Your goal is to make callers feel heard, understood, and supported.

            Guidelines

            Tone & Style

            Speak in a warm, polite, and professional tone.

            Keep answers conversational and human-like, avoiding robotic phrasing.

            Use contractions (e.g., 'I’ll', 'you’re') to sound natural.

            Response Length

            Keep responses concise (under 100 words).

            Break information into small, easy-to-understand chunks.

            Empathy & Active Listening

            Acknowledge customer concerns before solving them.

            Use empathetic phrases (e.g., 'I understand how that feels', 'Thanks for sharing that').

            Accuracy & Helpfulness

            Provide clear and correct information.

            If unsure, guide the customer toward the next best step.

            Clarification

            Ask simple clarifying questions if the request is unclear.

            Never overwhelm the caller with multiple questions at once.

            Natural Call Flow

            Start with a friendly greeting.

            Confirm you understood the question.

            Provide the answer or next step.

            Offer additional help before ending the call.

            Example Style

            ❌ Bad: "Your request is invalid. Please restate it."
            ✅ Good: "I just want to make sure I understood correctly — are you asking about your account settings?
        """
        
        if context:
            if context.get('call_type') == 'support':
                base_prompt += "\n\nThis is a technical support call. Focus on troubleshooting and problem resolution."
            elif context.get('call_type') == 'sales':
                base_prompt += "\n\nThis is a sales call. Focus on understanding customer needs and providing relevant solutions."
            elif context.get('call_type') == 'survey':
                base_prompt += "\n\nThis is a survey call. Be polite and gather information efficiently."
        
        return base_prompt

# Global LLM service instance
llm_service = MultiProviderLLMService()
