# src\services\llm_service.py

from typing import Dict, List, Optional, Any
import logging
import asyncio
import json
import httpx
from config.settings import settings
from services.prompt_template_service import prompt_template_service
# Import function calling components
from functions.functions_mainfest import format_tools_for_openai, get_tool_by_name
from functions.function_executor import function_executor

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
        
        # Function calling support
        self.enable_function_calling = True
        self.max_function_call_rounds = 3  # Prevent infinite loops
    
    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        context: Optional[Dict[str, Any]] = None,
        enable_functions: bool = True,
        call_sid: Optional[str] = None
    ) -> str:
        """
        Generate a response from the best available LLM provider with function calling support
        
        Args:
            messages: Conversation history
            context: Additional context for the LLM
            enable_functions: Whether to enable function calling
            call_sid: Twilio call SID (needed for transferCall function)
        """
        # Try providers in order
        for provider in self.provider_priority:
            try:
                if provider == 'claude' and self.claude_api_key:
                    return await self._generate_claude_response(messages, context, enable_functions, call_sid)
                elif provider == 'openai' and self.openai_api_key:
                    return await self._generate_openai_response(messages, context, enable_functions, call_sid)
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
        return await self.generate_response(messages, context, enable_functions=False)
    
    async def analyze_sentiment(self, text: str) -> Dict[str, Any]:
        """Analyze sentiment of user input"""
        try:
            prompt = f"Analyze the sentiment of the following text. Return only a JSON object with 'sentiment' (positive/negative/neutral) and 'confidence' (0-1). Text: {text}"
            response = await self.generate_response([
                {"role": "user", "content": prompt}
            ], enable_functions=False)
            
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
    
    async def _generate_claude_response(
        self,
        messages: List[Dict[str, str]],
        context: Optional[Dict[str, Any]] = None,
        enable_functions: bool = True,
        call_sid: Optional[str] = None
    ) -> str:
        """Generate response using Claude API (Note: Claude doesn't support native function calling yet)"""
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
                        "model": "claude-3-haiku-20240307",
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
    
    async def _generate_openai_response(
        self,
        messages: List[Dict[str, str]],
        context: Optional[Dict[str, Any]] = None,
        enable_functions: bool = True,
        call_sid: Optional[str] = None
    ) -> str:
        """Generate response using OpenAI API with function calling support"""
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=self.openai_api_key)
            
            # Prepare system message
            system_message = {
                "role": "system",
                "content": self._get_system_prompt(context)
            }
            
            # Combine system message with conversation history
            full_messages = [system_message] + messages[-10:]  # Keep last 10 messages
            
            # Get tools if function calling is enabled
            tools = None
            if enable_functions and self.enable_function_calling:
                tools = format_tools_for_openai()
            
            # Make initial API call
            response = await client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None,
                stream=False
            )
            
            # Handle function calling
            if enable_functions and response.choices[0].message.tool_calls:
                return await self._handle_function_calls(
                    client,
                    full_messages,
                    response,
                    call_sid
                )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"OpenAI API failed: {str(e)}")
            raise
    
    async def _handle_function_calls(
        self,
        client: Any,
        messages: List[Dict[str, str]],
        response: Any,
        call_sid: Optional[str] = None
    ) -> str:
        """Handle function calls from OpenAI response"""
        try:
            function_call_count = 0
            current_messages = messages.copy()
            current_response = response
            
            while (
                current_response.choices[0].message.tool_calls and 
                function_call_count < self.max_function_call_rounds
            ):
                function_call_count += 1
                
                # Add assistant message with tool calls to conversation
                assistant_message = current_response.choices[0].message
                current_messages.append({
                    "role": "assistant",
                    "content": assistant_message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        for tc in assistant_message.tool_calls
                    ]
                })
                
                # Execute each function call
                for tool_call in assistant_message.tool_calls:
                    function_name = tool_call.function.name
                    function_args = json.loads(tool_call.function.arguments)
                    
                    logger.info(f"Executing function: {function_name} with args: {function_args}")
                    
                    # Add call_sid to function args if needed (for transferCall)
                    if function_name == 'transferCall' and call_sid:
                        function_args['callSid'] = call_sid
                    
                    # Get the tool definition to check for 'say' field
                    tool_def = get_tool_by_name(function_name)
                    say_message = tool_def['function'].get('say') if tool_def else None
                    
                    # Execute the function
                    function_result = await function_executor.execute_function(
                        function_name,
                        function_args
                    )
                    
                    logger.info(f"Function {function_name} result: {function_result}")
                    
                    # Add function result to messages
                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": function_name,
                        "content": function_result
                    })
                    
                    # If there's a 'say' message, add it to context
                    if say_message:
                        current_messages.append({
                            "role": "system",
                            "content": f"[System instruction: Say this to the user: {say_message}]"
                        })
                
                # Get next response from OpenAI with function results
                current_response = await client.chat.completions.create(
                    model=self.model,
                    messages=current_messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    tools=format_tools_for_openai(),
                    tool_choice="auto",
                    stream=False
                )
                
                # If no more tool calls, break
                if not current_response.choices[0].message.tool_calls:
                    break
            
            # Return final response
            return current_response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"Error handling function calls: {str(e)}")
            return "I apologize, but I encountered an error while processing your request. Could you please try again?"
    
    async def _generate_fallback_response(
        self,
        messages: List[Dict[str, str]],
        context: Optional[Dict[str, Any]] = None
    ) -> str:
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
            
            # Transfer request patterns
            elif any(word in last_message_lower for word in ['speak to', 'talk to', 'transfer', 'human', 'agent', 'representative']):
                return "I understand you'd like to speak with a live agent. Let me transfer you now."
            
            # Default response
            else:
                return "I understand. Could you please tell me more about how I can help you?"
        except Exception as e:
            logger.error(f"Fallback response failed: {str(e)}")
            return "I'm here to help. How can I assist you today?"
    
    def _get_system_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """Get system prompt based on context"""
        base_prompt = prompt_template_service.build_system_prompt_with_guardrails(
            agent=agent,
            call_type="incoming"
        )
        
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
