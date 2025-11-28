# src/services/intent_detection_service.py

import logging
from typing import Dict, Optional
from openai import AsyncOpenAI
from config.settings import settings
import json

logger = logging.getLogger(__name__)

class IntentDetectionService:
    """AI-powered intent and sentiment detection for sales calls"""
    
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = "gpt-4o-mini"
    
    async def detect_customer_intent(
        self,
        customer_message: str,
        conversation_history: list = None,
        call_type: str = "incoming"
    ) -> Dict:
        """
        Use AI to detect customer intent, sentiment, and buying readiness
        
        Returns:
            {
                'intent_type': 'strong_buying' | 'soft_interest' | 'objection' | 'rejection' | 'neutral' | 'question',
                'sentiment': 'positive' | 'negative' | 'neutral',
                'buying_readiness': 0-100 (percentage),
                'should_book': bool,
                'should_persuade': bool,
                'should_end_call': bool,
                'objection_type': str (if objection detected),
                'reasoning': str,
                'suggested_response_tone': str
            }
        """
        
        try:
            # Build conversation context
            context_messages = []
            if conversation_history and len(conversation_history) > 0:
                for msg in conversation_history[-4:]:  # Last 2 exchanges
                    context_messages.append(f"{msg['role'].upper()}: {msg['content']}")
            
            conversation_context = "\n".join(context_messages) if context_messages else "No prior conversation"
            
            # prompt for intent detection
            system_prompt = f"""You are an expert at analyzing customer intent in {call_type} sales calls.

**CRITICAL CONTEXT AWARENESS:**
- When customer says "yes", "okay", "sure" etc., you MUST understand what they're agreeing to
- Look at the LAST AGENT MESSAGE to understand context
- "Yes" to hearing more information ≠ "Yes" to buying/booking
- Only mark as strong_buying if customer explicitly confirms they want to PURCHASE/BOOK/SIGN UP

**Intent Types:**
1. **strong_buying**: EXPLICIT confirmation to purchase/book/commit
   - Examples: "yes, book it", "I'll take it", "sign me up", "let's schedule it"
   - NOT: "yes, tell me more", "yes, I'm interested", "yes, I'll hear about it"

2. **soft_interest**: Interested/willing to learn more but NOT ready to commit
   - Examples: "yes, tell me more", "I'm interested", "yes, I'll hear about it", "okay, what is it?"

3. **objection**: Has concerns but not rejecting
   - Examples: "too expensive", "not sure", "need to think"

4. **rejection**: Clear refusal
   - Examples: "not interested", "no thanks", "don't call again"

5. **question**: Asking for information
   - Examples: "what's included?", "how much?", "when is it available?"

6. **neutral**: General response
   - Examples: "okay", "I see", "hmm"

**Buying Readiness Scale (0-100):**
- **80-100**: EXPLICITLY agreed to book/purchase/sign up
- **60-79**: Interested in learning more, asked good questions
- **40-59**: Willing to listen, mild interest
- **20-39**: Skeptical, has objections
- **0-19**: Not interested or hostile

**IMPORTANT RULES:**
1. If customer says "yes" and last agent question was about hearing more info → soft_interest (50-60%)
2. If customer says "yes" and last agent question was about booking → strong_buying (85-100%)
3. Context matters more than keywords!

**Output ONLY valid JSON:**
{{
    "intent_type": "strong_buying|soft_interest|objection|rejection|question|neutral",
    "sentiment": "positive|negative|neutral",
    "buying_readiness": 0-100,
    "should_book": true/false,
    "should_persuade": true/false,
    "should_end_call": true/false,
    "objection_type": "price|time|trust|need|other|none",
    "reasoning": "brief explanation including what customer is agreeing to",
    "suggested_response_tone": "enthusiastic|empathetic|informative|polite_farewell",
    "customer_agreed_to": "hearing_more|booking|nothing|rejection"
}}"""

            user_prompt = (
                f"**Conversation Context:**\n"
                f"{conversation_context}\n\n"
                f"**Last Agent Message (IMPORTANT for context):**\n"
                f'"{last_agent_question}"\n\n'
                f"**Customer's Latest Response:**\n"
                f'"{customer_message}"\n\n'
                f"**Analysis Task:**\n"
                f"1. What did the agent last ask the customer?\n"
                f"2. What is the customer responding to?\n"
                f"3. Is this agreement to BOOK or just to HEAR MORE?\n\n"
                f"Provide the JSON output."
            )


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
            
            # Parse JSON response
            result = json.loads(result_text)
            
            logger.info(f"AI Intent Detection:")
            logger.info(f"Intent: {result.get('intent_type')}")
            logger.info(f"Sentiment: {result.get('sentiment')}")
            logger.info(f"Buying Readiness: {result.get('buying_readiness')}%")
            logger.info(f"Reasoning: {result.get('reasoning')}")
            
            return result
            
        except Exception as e:
            logger.error(f"AI intent detection failed: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            
            # Fallback to neutral
            return {
                'intent_type': 'neutral',
                'sentiment': 'neutral',
                'buying_readiness': 50,
                'should_book': False,
                'should_persuade': True,
                'should_end_call': False,
                'objection_type': 'none',
                'reasoning': 'AI detection failed, defaulting to neutral',
                'suggested_response_tone': 'informative'
            }

# Global instance
intent_detection_service = IntentDetectionService()
