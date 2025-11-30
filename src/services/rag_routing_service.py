# src/services/rag_routing_service.py

import logging
from typing import Dict, Optional
from openai import AsyncOpenAI
from config.settings import settings
import json

logger = logging.getLogger(__name__)

class RAGRoutingService:
    """AI-powered intelligent routing for RAG queries"""
    
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = "gpt-4o-mini"
    
    async def should_retrieve_documents(
        self,
        user_message: str,
        conversation_history: list,
        call_type: str = "incoming",
        agent_context: dict = None
    ) -> Dict:
        """
        Use AI to intelligently decide if document retrieval is needed
        
        Returns:
            {
                'needs_documents': bool,
                'reasoning': str,
                'confidence': float,
                'response_strategy': 'direct_canned' | 'conversation_context' | 'document_retrieval'
            }
        """
        
        try:
            # Build conversation context
            recent_exchanges = []
            if conversation_history:
                for msg in conversation_history[-6:]:  # Last 3 exchanges
                    recent_exchanges.append(f"{msg['role'].upper()}: {msg['content']}")
            
            conversation_context = "\n".join(recent_exchanges) if recent_exchanges else "This is the first message"
            
            # Get business context
            business_info = ""
            if agent_context:
                company_name = agent_context.get('name', 'the company')
                business_context = agent_context.get('additional_context', {}).get('businessContext', '')
                if business_context:
                    business_info = f"\nBusiness: {company_name} - {business_context}"
            
            system_prompt = f"""You are an intelligent routing AI that decides if document retrieval (RAG) is needed to answer a customer's question.

**Your Decision Framework:**

Analyze the customer's message and conversation history to determine the BEST way to respond:

**Option 1: DIRECT_CANNED** (No API calls needed)
Use when:
- Simple greetings ("hi", "hello")
- Simple farewells ("bye", "thanks")
- Very simple acknowledgments with NO context ("ok" as first message)

**Option 2: CONVERSATION_CONTEXT** (Use LLM with conversation history only, NO document retrieval)
Use when:
- Customer is continuing an active conversation
- Question relates to what was JUST discussed
- Customer says "tell me more" about something you already mentioned
- Customer acknowledges and you need to continue the flow
- Customer asks follow-up questions about current topic
- Booking-related actions (booking doesn't need company docs)
- Questions can be answered from conversation alone

**Option 3: DOCUMENT_RETRIEVAL** (Full RAG - search company documents)
Use when:
- Customer asks about NEW topics not discussed yet
- Customer requests specific information (pricing, features, policies, procedures)
- Customer asks "what is..." or "tell me about..." something NEW
- Question requires company-specific knowledge not in conversation
- Technical questions about products/services
- First substantive message that needs real information

**KEY PRINCIPLES:**
1. **Conversation Momentum**: If actively discussing something, keep going with conversation context
2. **New Topics**: If customer introduces something NEW, retrieve documents
3. **Depth Required**: If customer asks for details/specifics, retrieve documents
4. **Efficiency**: Don't retrieve documents if conversation history has the answer

**Context:**
Call Type: {call_type}
Conversation Length: {len(conversation_history)} messages
{business_info}

**Output ONLY valid JSON:**
{{
    "needs_documents": true/false,
    "response_strategy": "direct_canned|conversation_context|document_retrieval",
    "reasoning": "brief explanation of why this strategy is best",
    "confidence": 0.0-1.0,
    "topic_continuity": "continuing|new_topic|greeting|farewell",
    "can_answer_from_history": true/false
}}"""

            user_prompt = f"""**Recent Conversation:**
{conversation_context}

**Customer's Latest Message:**
"{user_message}"

**Analysis Questions:**
1. Is this a new topic or continuing the current conversation?
2. Can this be answered from the conversation history alone?
3. Does this require looking up company-specific information?
4. What's the most efficient way to respond?

Provide the JSON decision."""

            # Call OpenAI
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            
            result_text = response.choices[0].message.content
            result = json.loads(result_text)
            
            logger.info(f"🎯 RAG Routing Decision:")
            logger.info(f"   Strategy: {result.get('response_strategy')}")
            logger.info(f"   Needs Documents: {result.get('needs_documents')}")
            logger.info(f"   Confidence: {result.get('confidence')}")
            logger.info(f"   Reasoning: {result.get('reasoning')}")
            
            return result
            
        except Exception as e:
            logger.error(f"RAG routing failed: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            
            # Safe fallback - use conversation context
            return {
                'needs_documents': False,
                'response_strategy': 'conversation_context',
                'reasoning': 'Routing service failed, defaulting to conversation context',
                'confidence': 0.5,
                'topic_continuity': 'unknown',
                'can_answer_from_history': True
            }

# Global instance
rag_routing_service = RAGRoutingService()