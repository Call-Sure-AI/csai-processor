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

    def generate_outbound_sales_greeting(
        self,
        agent: Dict,
        customer_name: str = "",
        campaign_id: str = ""
    ) -> str:
        """
        Generate dynamic outbound sales greeting based on agent configuration
        
        Uses agent's prompt, businessContext, and roleDescription to create
        a natural, context-aware greeting for outbound calls.
        """
        try:
            additional_context = agent.get('additional_context', {})

            prompt = agent.get('prompt', '')
            ai_name = self._extract_name_from_prompt(prompt)

            business_context = additional_context.get('businessContext', '')
            role_description = additional_context.get('roleDescription', '')

            company_name = self._extract_company_name(agent.get('name', ''), business_context)

            company_intro = self._build_company_intro(business_context)

            if customer_name:
                greeting = (
                    f"Hello {customer_name}! This is {ai_name} calling from {company_name}. "
                    f"{company_intro} "
                    f"I'm reaching out to discuss how we can help you. "
                    f"Do you have a moment to talk?"
                )
            else:
                greeting = (
                    f"Hello! This is {ai_name} from {company_name}. "
                    f"{company_intro} "
                    f"I'm calling to see how we can assist you. "
                    f"Do you have a minute?"
                )
            
            logger.info(f"Generated dynamic greeting for {ai_name} from {company_name}")
            return greeting
            
        except Exception as e:
            logger.error(f"Error generating sales greeting: {str(e)}")
            return f"Hello {customer_name if customer_name else ''}! This is calling from your service provider. How may I help you today?"
        
        def _extract_name_from_prompt(self, prompt: str) -> str:
            try:
                if "You are" in prompt:
                    parts = prompt.split("You are")[1].split(",")[0].strip()
                    name = parts.split()[0]
                    return name
                return "Sarah"
            except:
                return "Sarah"
        
        def _extract_company_name(self, agent_name: str, business_context: str) -> str:
            """Extract company name from agent name or business context"""
            try:
                if "Master Agent" in agent_name:
                    company = agent_name.replace("Master Agent", "").strip()
                    if company:
                        return company

                if business_context:
                    for keyword in ["at ", "from ", "for "]:
                        if keyword in business_context:
                            parts = business_context.split(keyword)
                            if len(parts) > 1:
                                next_words = parts[1].split()[0:2]
                                company = " ".join(next_words).rstrip(",.")
                                if company and len(company) > 2:
                                    return company
                
                return "our company"
            except:
                return "our company"
        
        def _build_company_intro(self, business_context: str) -> str:
            """Build company introduction from business context"""
            try:
                if not business_context:
                    return "We provide comprehensive services to help you."
                
                if "provides" in business_context.lower():
                    intro_part = business_context.split("provides")[1].split(".")[0].strip()
                    return f"We provide {intro_part}."
                elif "offers" in business_context.lower():
                    intro_part = business_context.split("offers")[1].split(".")[0].strip()
                    return f"We offer {intro_part}."
                else:
                    first_sentence = business_context.split(".")[0].strip()
                    if first_sentence.split()[0].endswith("'s") or first_sentence.split()[0] in ["JamunJar", "We"]:
                        first_sentence = " ".join(first_sentence.split()[1:])
                    return first_sentence + "."
                    
            except Exception as e:
                logger.error(f"Error building company intro: {str(e)}")
                return "We're here to help you with your needs."



# Global instance
prompt_template_service = PromptTemplateService()
