# src/services/prompt_template_service.py

import logging
from typing import Dict, List, Optional
import json

logger = logging.getLogger(__name__)

class PromptTemplateService:
    def __init__(self):
        pass
    
    # ========== REQUIREMENT i) Sales-Focused Outbound Greeting ==========
    
    def generate_outbound_sales_greeting(
        self,
        agent: Dict,
        customer_name: str = "",
        campaign_id: str = ""
    ) -> str:
        """
        Generate intelligent sales greeting that asks about specific services
        based on agent's businessContext and prompt
        """
        try:
            additional_context = agent.get('additional_context', {})
            prompt = agent.get('prompt', '')
            
            # Extract details
            ai_name = self._extract_name_from_prompt(prompt)
            company_name = self._extract_company_name(agent.get('name', ''), additional_context.get('businessContext', ''))
            
            # Extract services from businessContext
            services = self._extract_services_from_context(additional_context.get('businessContext', ''))
            
            # Extract target audience
            target_audience = self._extract_target_audience(additional_context.get('businessContext', ''))
            
            # Build service-focused greeting
            if services:
                service_pitch = self._build_service_pitch(services, target_audience)
            else:
                service_pitch = "our services that could benefit you"
            
            # Generate greeting
            if customer_name:
                greeting = (
                    f"Hello {customer_name}! This is {ai_name} calling from {company_name}. "
                    f"I'm reaching out because {service_pitch}. "
                    f"Would you be interested in learning more about this?"
                )
            else:
                greeting = (
                    f"Hello! This is {ai_name} from {company_name}. "
                    f"We specialize in {service_pitch}. "
                    f"Can I share how we can help you?"
                )
            
            logger.info(f"Generated sales greeting for {ai_name} from {company_name}")
            return greeting
            
        except Exception as e:
            logger.error(f"Error generating sales greeting: {str(e)}")
            return f"Hello {customer_name if customer_name else ''}! This is calling from your service provider. How may I help you today?"
    
    def _extract_services_from_context(self, business_context: str) -> List[str]:
        """Extract service offerings from businessContext"""
        services = []
        
        # Look for common patterns indicating services
        # Pattern 1: "provides X, Y, and Z"
        if "provides" in business_context.lower():
            service_text = business_context.split("provides")[1].split(".")[0]
            # Extract items in parentheses
            import re
            parenthetical = re.findall(r'\(([^)]+)\)', service_text)
            for item in parenthetical:
                services.extend([s.strip() for s in item.split(',')])
        
        # Pattern 2: "offers X and Y"
        if "offers" in business_context.lower():
            service_text = business_context.split("offers")[1].split(".")[0]
            import re
            parenthetical = re.findall(r'\(([^)]+)\)', service_text)
            for item in parenthetical:
                services.extend([s.strip() for s in item.split(',')])
        
        return services[:3]  # Return top 3 services
    
    def _extract_target_audience(self, business_context: str) -> str:
        """Extract target audience from businessContext"""
        # Look for age groups, demographics
        import re
        
        age_match = re.search(r'(\d+)\+', business_context)
        if age_match:
            return f"people {age_match.group(1)} years and above"
        
        if "individuals" in business_context.lower():
            return "individuals like yourself"
        
        return "you"
    
    def _build_service_pitch(self, services: List[str], target_audience: str) -> str:
        """Build intelligent service pitch"""
        if len(services) >= 2:
            return (
                f"we offer {services[0]}, {services[1]}, "
                f"and more specifically designed for {target_audience}"
            )
        elif len(services) == 1:
            return f"we provide {services[0]} for {target_audience}"
        else:
            return f"we have services designed for {target_audience}"
    
    # ========== REQUIREMENT ii) Guardrails for Out-of-Context Queries ==========
    
    def build_system_prompt_with_guardrails(
        self,
        agent: Dict,
        call_type: str = "incoming"
    ) -> str:
        """
        Build comprehensive system prompt with guardrails
        """
        base_prompt = agent.get('prompt', '')
        additional_context = agent.get('additional_context', {})
        
        business_context = additional_context.get('businessContext', '')
        role_description = additional_context.get('roleDescription', '')
        tone = additional_context.get('tone', 'professional')
        
        # Build guardrails section
        guardrails = self._build_guardrails_section(agent, call_type)
        
        # Build complete prompt
        system_prompt = f"""
{base_prompt}

BUSINESS CONTEXT:
{business_context}

YOUR ROLE:
{role_description}

TONE: {tone}

{guardrails}

RESPONSE GUIDELINES:
1. Keep responses concise (2-3 sentences maximum)
2. Use natural, conversational language
3. Ask clarifying questions when needed
4. Focus on helping the customer efficiently
5. Be empathetic and professional
"""
        
        return system_prompt.strip()
    
    def _build_guardrails_section(self, agent: Dict, call_type: str) -> str:
        """Build dynamic guardrails based on agent configuration"""
        
        additional_context = agent.get('additional_context', {})
        business_context = additional_context.get('businessContext', '')
        
        # Extract company name and services
        company_name = self._extract_company_name(agent.get('name', ''), business_context)
        services = self._extract_services_from_context(business_context)
        
        services_list = ", ".join(services) if services else "our services"
        
        guardrails = f"""
GUARDRAILS - STRICT RULES YOU MUST FOLLOW:

1. SCOPE BOUNDARIES:
   - You can ONLY discuss topics related to: {services_list}
   - If asked about topics outside {company_name}'s services, politely redirect:
     "I appreciate your question, but I'm specifically here to help with {services_list}. 
      Is there anything related to these services I can assist you with?"
   
   - For completely unrelated queries (weather, politics, general knowledge):
     "I'm designed to help specifically with {company_name}'s services. 
      Let's focus on how I can assist you with {services_list}."

2. PROHIBITED TOPICS - NEVER DISCUSS:
   - Competitor companies or their services
   - Personal opinions on politics, religion, or controversial topics
   - Medical diagnoses (you can discuss our services but not diagnose)
   - Legal advice (you can explain our policies but not give legal counsel)
   - Financial advice beyond our service offerings
   
   If asked about prohibited topics:
   "I'm not able to provide advice on that topic, but I can help you with {company_name}'s services. 
    Would you like to know more about what we offer?"

3. SENSITIVE INFORMATION:
   - NEVER ask for: passwords, credit card CVV, SSN, or sensitive personal data
   - For account verification, say: "For security, I'll need to verify a few details. 
     Can you confirm your registered phone number or email address?"

4. UNCERTAINTY HANDLING:
   - If you don't know something: "That's a great question. Let me check our resources for the most accurate information."
   - NEVER make up information or pricing
   - Offer to transfer to a specialist if needed

5. {"SALES" if call_type == "outgoing" else "SERVICE"} FOCUS:
   {"- Always guide conversation toward service enrollment or inquiry" if call_type == "outgoing" else "- Focus on resolving the customer's issue efficiently"}
   - Identify customer needs and match them to our services
   - Use natural transitions, not aggressive sales tactics
"""
        
        return guardrails.strip()
    
    # ========== REQUIREMENT iii) RAG Query Acknowledgment ==========
    
    def generate_rag_acknowledgment(self, user_query: str, agent: Dict) -> str:
        """
        Generate intelligent acknowledgment before RAG query based on user's question
        """
        query_lower = user_query.lower()
        
        # Analyze query intent
        if any(word in query_lower for word in ['price', 'cost', 'fee', 'charge', 'payment', 'amount']):
            return "Let me pull up our current pricing information for you."
        
        elif any(word in query_lower for word in ['policy', 'terms', 'condition', 'coverage', 'include']):
            return "Let me check the policy details for you. One moment please."
        
        elif any(word in query_lower for word in ['available', 'offer', 'provide', 'service', 'plan']):
            return "Great question! Let me look up what we have available for you."
        
        elif any(word in query_lower for word in ['how', 'process', 'procedure', 'step']):
            return "Let me walk you through that process. Give me just a moment."
        
        elif any(word in query_lower for word in ['when', 'time', 'schedule', 'appointment']):
            return "Let me check our availability for you."
        
        elif any(word in query_lower for word in ['benefit', 'advantage', 'feature']):
            return "Let me get you the details on those benefits."
        
        elif any(word in query_lower for word in ['compare', 'difference', 'versus', 'vs']):
            return "Good question! Let me gather that comparison information for you."
        
        else:
            # Default intelligent acknowledgment
            return "Let me find the most accurate information for you. Just a moment."
    
    # ========== REQUIREMENT iv) Sentiment & Urgency Detection ==========
    
    def detect_sentiment_and_urgency(self, user_message: str, agent: Dict) -> Dict:
        """
        Detect sentiment and urgency from user message
        Returns: {
            'sentiment': 'positive'|'negative'|'neutral',
            'urgency': 'high'|'medium'|'low',
            'keywords': [...],
            'suggested_action': '...'
        }
        """
        message_lower = user_message.lower()
        
        # Define dynamic urgency keywords based on business context
        additional_context = agent.get('additional_context', {})
        business_context = additional_context.get('businessContext', '').lower()
        
        # High urgency keywords (universal + context-specific)
        high_urgency_keywords = [
            'emergency', 'urgent', 'immediately', 'asap', 'critical',
            'lost', 'stolen', 'fraud', 'unauthorized', 'hacked',
            'dying', 'death', 'accident', 'injury', 'pain',
            'blocked', 'locked out', 'can\'t access', 'not working'
        ]

        buying_intent_keywords = [
            'yes', 'yeah', 'sure', 'okay', 'ok', 'definitely', 'absolutely',
            'interested', 'want', 'would like', 'book', 'schedule', 'sign up',
            'enroll', 'register', 'purchase', 'buy', 'get started', 'lets do it',
            'sounds good', 'that works', 'ill take it', 'count me in'
        ]
        
        # Add context-specific urgencies
        if 'health' in business_context or 'medical' in business_context:
            high_urgency_keywords.extend([
                'chest pain', 'bleeding', 'severe pain', 'difficulty breathing',
                'unconscious', 'allergic reaction'
            ])
        
        if 'financial' in business_context or 'insurance' in business_context:
            high_urgency_keywords.extend([
                'card lost', 'card stolen', 'fraud alert', 'unauthorized transaction',
                'account hacked', 'identity theft'
            ])
        
        if 'diagnostic' in business_context or 'lab' in business_context:
            high_urgency_keywords.extend([
                'test results', 'abnormal results', 'urgent report'
            ])
        
        # Medium urgency keywords
        medium_urgency_keywords = [
            'soon', 'today', 'this week', 'important', 'need help',
            'problem', 'issue', 'trouble', 'concern', 'worried'
        ]
        
        # Negative sentiment keywords
        negative_keywords = [
            'angry', 'frustrated', 'upset', 'disappointed', 'terrible',
            'awful', 'horrible', 'worst', 'unhappy', 'dissatisfied',
            'complaint', 'unacceptable', 'ridiculous'
        ]
        
        # Positive sentiment keywords
        positive_keywords = [
            'thank', 'great', 'excellent', 'good', 'happy', 'satisfied',
            'appreciate', 'wonderful', 'perfect', 'love', 'amazing'
        ]
        
        rejection_phrases = [
            "don't want", "dont want", "not interested", "no thanks", "not for me",
            "don't need", "dont need", "not now", "maybe later", "call back later",
            "not looking", "already have", "no thank you", "not today", "too busy",
            "stop calling", "remove me", "take me off", "unsubscribe", "leave me alone"
        ]

        # Detection logic
        detected_rejection = any(phrase in message_lower for phrase in rejection_phrases)
        detected_high_urgency = [kw for kw in high_urgency_keywords if kw in message_lower]
        detected_medium_urgency = [kw for kw in medium_urgency_keywords if kw in message_lower]
        detected_negative = [kw for kw in negative_keywords if kw in message_lower]
        detected_positive = [kw for kw in positive_keywords if kw in message_lower]

        if detected_rejection:
            detected_buying_intent = []
            buying_intent = False
            logger.info(f"Rejection detected: '{message_lower}' - No buying intent")
        else:
            detected_buying_intent = [kw for kw in buying_intent_keywords if kw in message_lower]
            buying_intent = len(detected_buying_intent) > 0
        
        # Determine urgency level
        if detected_high_urgency:
            urgency = 'high'
            urgency_keywords = detected_high_urgency
        elif detected_medium_urgency:
            urgency = 'medium'
            urgency_keywords = detected_medium_urgency
        else:
            urgency = 'low'
            urgency_keywords = []
        
        # Determine sentiment
        if detected_rejection or detected_negative:
            sentiment = 'negative'
            sentiment_keywords = detected_negative if detected_negative else ['rejection']
        elif detected_positive:
            sentiment = 'positive'
            sentiment_keywords = detected_positive
        elif detected_positive or detected_buying_intent:
            sentiment = 'positive'
            sentiment_keywords = detected_positive + detected_buying_intent
        else:
            sentiment = 'neutral'
            sentiment_keywords = []
        
        # Generate suggested action
        suggested_action = self._generate_urgency_response(
            urgency, sentiment, urgency_keywords, agent
        )
        
        result = {
            'sentiment': sentiment,
            'urgency': urgency,
            'urgency_keywords': urgency_keywords,
            'sentiment_keywords': sentiment_keywords,
            'suggested_action': suggested_action,
            'buying_intent': len(detected_buying_intent) > 0,
            'buying_intent_keywords': detected_buying_intent,
            'rejection_detected': detected_rejection
        }
        
        logger.info(f"Sentiment Analysis: {result}")
        return result
    
    def _generate_urgency_response(
        self,
        urgency: str,
        sentiment: str,
        keywords: List[str],
        agent: Dict
    ) -> str:
        """Generate appropriate response based on urgency and sentiment"""
        
        ai_name = self._extract_name_from_prompt(agent.get('prompt', ''))
        
        if urgency == 'high':
            if any(kw in ['lost', 'stolen', 'fraud'] for kw in keywords):
                return (
                    f"I understand this is urgent regarding security. "
                    f"Let me immediately look up what we can do to help you right away."
                )
            elif any(kw in ['emergency', 'critical', 'severe'] for kw in keywords):
                return (
                    f"I can hear this is critical. Let me get you the help you need right away. "
                    f"I'm connecting you with our emergency support team immediately."
                )
            else:
                return (
                    f"I understand the urgency of your situation. "
                    f"Let me handle this as a priority and get you immediate assistance."
                )
        
        elif urgency == 'medium':
            return (
                f"I understand this is important to you. "
                f"Let me make sure we address this promptly."
            )
        
        elif sentiment == 'negative':
            return (
                f"I'm sorry to hear about your experience. "
                f"Let me help resolve this for you right away."
            )
        
        else:
            return None
    
    def _extract_name_from_prompt(self, prompt: str) -> str:
        """Extract AI name from prompt"""
        try:
            if "You are" in prompt:
                parts = prompt.split("You are")[1].split(",")[0].strip()
                name = parts.split()[0]
                return name
            return "Sarah"
        except:
            return "Sarah"
    
    def _extract_company_name(self, agent_name: str, business_context: str) -> str:
        """Extract company name"""
        try:
            if "Master Agent" in agent_name:
                company = agent_name.replace("Master Agent", "").strip()
                if company:
                    return company
            
            if business_context:
                for keyword in ["at ", "from "]:
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
    
    def generate_greeting(self, agent: Dict) -> str:
        """Generate greeting for incoming calls"""
        try:
            ai_name = self._extract_name_from_prompt(agent.get('prompt', ''))
            additional_context = agent.get('additional_context', {})
            company_name = self._extract_company_name(
                agent.get('name', ''),
                additional_context.get('businessContext', '')
            )
            
            return f"Hello! This is {ai_name} from {company_name}. How may I help you today?"
        except:
            return "Hello! How may I help you today?"


# Singleton instance
prompt_template_service = PromptTemplateService()
