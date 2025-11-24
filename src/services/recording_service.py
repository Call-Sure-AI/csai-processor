import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class PromptTemplateService:
    """Generate dynamic system prompts from agent configuration"""
    
    @staticmethod
    def generate_system_prompt(agent_config: Dict) -> str:
        """
        Generate a complete system prompt from agent configuration
        
        Args:
            agent_config: Agent configuration dict from API
            
        Returns:
            Formatted system prompt string
        """
        try:
            # Extract fields from agent config
            agent_name = agent_config.get('name', 'Assistant')
            agent_type = agent_config.get('type', 'base')
            base_prompt = agent_config.get('prompt', '')
            
            # Extract additional context
            additional_context = agent_config.get('additional_context', {})
            tone = additional_context.get('tone', 'professional')
            language = additional_context.get('language', 'english')
            gender = additional_context.get('gender', 'neutral')
            business_context = additional_context.get('businessContext', '')
            role_description = additional_context.get('roleDescription', '')
            
            # Extract advanced settings
            max_tokens = agent_config.get('max_response_tokens', 300)
            temperature = agent_config.get('temperature', 0.7)
            
            # Build dynamic system prompt
            system_prompt = f"""You are {agent_name}, a {tone} voice assistant on a live phone call.

**Your Role**: {role_description if role_description else 'Customer service representative'}

**Business Context**: 
{business_context if business_context else 'Provide helpful customer support'}

**Communication Guidelines**:
- Tone: {tone.title()}
- Language: {language.title()}
- Gender: {gender.title()}
- Keep responses conversational and under {max_tokens // 4} words
- Be empathetic, clear, and solution-focused
- Listen actively and acknowledge concerns before responding

**Important Rules**:
- Never mention competitor names or other companies
- Focus on addressing the customer's specific needs
- Use information from your knowledge base when available
- If uncertain, be honest and offer to escalate or get more information
- Maintain natural conversation flow - avoid robotic responses
- Ask clarifying questions when needed

**Your Specific Instructions**:
{base_prompt}

**Response Style**:
- Conversational and human-like
- Brief and to the point (aim for {max_tokens // 4}-{max_tokens // 3} words per response)
- Professional yet friendly
- Avoid jargon unless appropriate for the context

Remember: You are on a **live voice call**. Respond naturally as a human would in conversation."""

            logger.info(f"Generated system prompt for {agent_name} ({len(system_prompt)} chars)")
            return system_prompt.strip()
            
        except Exception as e:
            logger.error(f"Error generating system prompt: {str(e)}")

            return agent_config.get('prompt', 'You are a helpful assistant.')
    
    @staticmethod
    def generate_greeting(agent_config: Dict) -> str:
        """
        Generate opening greeting based on agent config
        
        Returns:
            Greeting message
        """
        try:
            additional_context = agent_config.get('additional_context', {})
            tone = additional_context.get('tone', 'professional')

            if tone in ['friendly', 'warm', 'casual']:
                return "Hello! Thank you for calling. How may I help you today?"
            elif tone in ['professional', 'formal']:
                return "Good day. How may I assist you today?"
            elif tone in ['efficient', 'direct']:
                return "Hello. How can I help you?"
            else:
                return "Thank you for calling. How may I assist you?"
                
        except Exception as e:
            logger.error(f"Error generating greeting: {str(e)}")
            return "Hello. How may I help you today?"
    
    @staticmethod
    def get_response_guidelines(agent_config: Dict) -> Dict:
        """
        Extract response formatting guidelines from config
        
        Returns:
            Dict with response constraints
        """
        return {
            'max_tokens': agent_config.get('max_response_tokens', 300),
            'temperature': agent_config.get('temperature', 0.7),
            'tone': agent_config.get('additional_context', {}).get('tone', 'professional'),
            'language': agent_config.get('additional_context', {}).get('language', 'english')
        }

# Global instance
prompt_template_service = PromptTemplateService()
