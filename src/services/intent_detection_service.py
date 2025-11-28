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

Analyze the customer's response and classify their intent precisely.

**Intent Types:**
1. **strong_buying**: Clear confirmation (e.g., "yes", "book it", "let's do it", "I'm ready")
2. **soft_interest**: Interested but not ready to commit (e.g., "tell me more", "I'm interested", "maybe")
3. **objection**: Has concerns but not rejecting (e.g., "too expensive", "not sure", "need to think")
4. **rejection**: Clear refusal (e.g., "not interested", "no thanks", "don't call again")
5. **question**: Asking for information (e.g., "what's included?", "how much?")
6. **neutral**: General response (e.g., "okay", "I see")

**Sentiment:**
- positive: Friendly, open, engaged
- negative: Hostile, annoyed, frustrated
- neutral: Indifferent, matter-of-fact

**Buying Readiness:** 0-100 scale
- 80-100: Ready to buy now
- 50-79: Interested, needs persuasion
- 20-49: Mild interest, significant persuasion needed
- 0-19: Not interested or hostile

**Output ONLY valid JSON** in this exact format:
{{
    "intent_type": "strong_buying|soft_interest|objection|rejection|question|neutral",
    "sentiment": "positive|negative|neutral",
    "buying_readiness": 0-100,
    "should_book": true/false,
    "should_persuade": true/false,
    "should_end_call": true/false,
    "objection_type": "price|time|trust|need|other|none",
    "reasoning": "brief explanation",
    "suggested_response_tone": "enthusiastic|empathetic|informative|polite_farewell"
}}"""

            user_prompt = f"""**Conversation Context:**
{conversation_context}

**Customer's Latest Response:**
"{customer_message}"

Analyze this response and provide the JSON output."""

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
