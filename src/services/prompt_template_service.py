# src/services/prompt_template_service.py
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class PromptTemplateService:
    """
    Simple utility service to generate dynamic greetings and prompts
    from agent configuration dictionaries (no database needed)
    """
    
    @staticmethod
    def generate_greeting(agent_config: Dict) -> str:
        """
        Generate opening greeting based on agent config
        WITHOUT mentioning company name
        
        Args:
            agent_config: Agent configuration dict from API
            
        Returns:
            Generic greeting message
        """
        try:
            additional_context = agent_config.get('additional_context', {})
            tone = additional_context.get('tone', 'professional')
            
            # Generate greeting based on tone
            if tone in ['friendly', 'warm', 'casual']:
                return "Hello! Thank you for calling. How may I help you today?"
            elif tone in ['professional', 'formal']:
                return "Good day. How may I assist you today?"
            elif tone in ['efficient', 'direct']:
                return "Hello. How can I help you?"
            else:
                return "Thank you for calling. How may I help you today?"
                
        except Exception as e:
            logger.error(f"Error generating greeting: {str(e)}")
            return "Hello. How may I help you today?"
    
    @staticmethod
    def get_response_guidelines(agent_config: Dict) -> Dict:
        """
        Extract response formatting guidelines from agent config
        
        Args:
            agent_config: Agent configuration dict from API
            
        Returns:
            Dict with response constraints (max_tokens, temperature, etc)
        """
        try:
            additional_context = agent_config.get('additional_context', {})
            
            return {
                'max_tokens': agent_config.get('max_response_tokens', 300),
                'temperature': agent_config.get('temperature', 0.7),
                'tone': additional_context.get('tone', 'professional'),
                'language': additional_context.get('language', 'english'),
                'gender': additional_context.get('gender', 'neutral')
            }
        except Exception as e:
            logger.error(f"Error extracting response guidelines: {str(e)}")
            return {
                'max_tokens': 300,
                'temperature': 0.7,
                'tone': 'professional',
                'language': 'english',
                'gender': 'neutral'
            }


# Global instance
prompt_template_service = PromptTemplateService()
