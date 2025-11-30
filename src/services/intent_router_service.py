# src\services\intent_router_service.py
import logging
from typing import Optional, Dict, List
from openai import AsyncOpenAI
from config.settings import settings

logger = logging.getLogger(__name__)

class IntentRouterService:
    """Route calls to appropriate specialized agents based on intent"""
    
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.current_agent = {}  # Track current agent per call_sid
        self.interaction_count = {}  # Track interaction count per call
        
    async def detect_intent(
        self,
        user_message: str,
        company_id: str,
        master_agent: Dict,
        available_agents: List[Dict[str, str]]
    ) -> Optional[str]:
        """
        Detect user intent and return the appropriate agent_id
        
        Args:
            user_message: What the user said
            company_id: Company ID
            master_agent: Master agent info
            available_agents: List of specialized agents
            
        Returns:
            agent_id to route to, or None to stay with master
        """
        try:
            # If no specialized agents, stay with master
            if not available_agents:
                logger.info("No specialized agents available, using master")
                return None
            
            # Build agent descriptions
            agent_descriptions = "\n".join([
                f"- {agent['name']} (ID: {agent['agent_id']}): {agent['description']}"
                for agent in available_agents
            ])
            
            # Intent detection prompt
            system_prompt = f"""You are an intelligent call routing AI.

**Available Specialists**:
{agent_descriptions}

**Your Task**:
Analyze the customer's request and determine which specialist can best help them.

**Rules**:
1. If the request matches a specialist's expertise, return their full agent ID
2. If it's a general greeting or unclear, return "MASTER"
3. Only return the agent ID or "MASTER" - nothing else

**Examples**:
"I need to book an appointment" → booking_agent_id
"Tell me about your services" → MASTER
"I want financial advice" → financial_agent_id"""


            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Customer: '{user_message}'\n\nWhich specialist?"}
                ],
                temperature=0.1,
                max_tokens=100
            )
            
            intent = response.choices[0].message.content.strip()
            
            logger.info(f"Intent: '{user_message[:50]}...' → {intent}")
            
            # Validate the response
            if intent == "MASTER":
                return None
            
            # Check if it's a valid agent_id
            valid_agents = {a['agent_id']: a for a in available_agents}
            
            # Exact match
            if intent in valid_agents:
                return intent
            
            # Partial match
            for agent_id in valid_agents:
                if intent in agent_id or agent_id.startswith(intent):
                    logger.info(f"Matched partial UUID: {intent} → {agent_id}")
                    return agent_id
            
            logger.warning(f"Invalid agent_id returned: {intent}, staying with MASTER")
            return None
            
        except Exception as e:
            logger.error(f"Error detecting intent: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def set_current_agent(self, call_sid: str, agent_id: str):
        """Set the current agent for a call"""
        self.current_agent[call_sid] = agent_id
        logger.info(f"Call {call_sid[:8]}... → Agent {agent_id[:8]}...")
    
    def get_current_agent(self, call_sid: str, default_agent_id: str) -> str:
        """Get the current agent for a call"""
        return self.current_agent.get(call_sid, default_agent_id)
    
    def increment_interaction(self, call_sid: str) -> int:
        """Increment and return interaction count"""
        self.interaction_count[call_sid] = self.interaction_count.get(call_sid, 0) + 1
        return self.interaction_count[call_sid]
    
    def clear_call(self, call_sid: str):
        """Clear call routing info"""
        self.current_agent.pop(call_sid, None)
        self.interaction_count.pop(call_sid, None)

# Global instance
intent_router_service = IntentRouterService()
