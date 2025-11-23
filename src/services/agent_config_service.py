import logging
from typing import List, Dict, Optional
import httpx
from config.settings import settings

logger = logging.getLogger(__name__)

class AgentConfigService:
    def __init__(self):
        self.api_base = "https://beta.callsure.ai"
        self.auth_token = settings.callsure_api_token
        self.user_id = None
        
        self.companies_cache = None
        self.agents_cache = None
        self.company_agents_map = {}
    
    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json"
        }
    
    async def _get_user_id(self) -> Optional[str]:
        """
        Get current user_id from /companies/me endpoint
        """
        if self.user_id:
            return self.user_id
        
        try:
            logger.info("Fetching user_id from /companies/me...")
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_base}/companies/me",
                    headers=self._get_headers(),
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    self.user_id = data.get("user_id")
                    
                    if self.user_id:
                        logger.info(f"User ID: {self.user_id}")
                        logger.info(f"Company: {data.get('name')}")
                        logger.info(f"Email: {data.get('email')}")
                        return self.user_id
                    else:
                        logger.error("No user_id in response")
                        return None
                else:
                    logger.error(f"Failed to fetch user info: {response.status_code}")
                    logger.error(f"Response: {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error fetching user_id: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def _load_companies(self) -> List[Dict]:
        """Load all companies for the user"""
        if self.companies_cache:
            return self.companies_cache
        
        try:
            user_id = await self._get_user_id()
            if not user_id:
                logger.error("Cannot load companies without user_id")
                return []
            
            logger.info(f"Loading companies for user {user_id}...")
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_base}/companies/user/{user_id}",
                    headers=self._get_headers(),
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    companies = response.json()
                    self.companies_cache = companies
                    logger.info(f"Loaded {len(companies)} companies")

                    for company in companies[:3]:
                        logger.info(f"- {company.get('name')} ({company.get('id')[:8]}...)")
                    
                    return companies
                else:
                    logger.error(f"Failed to fetch companies: {response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error fetching companies: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    async def _load_agents(self) -> List[Dict]:
        """Load all agents for the user"""
        if self.agents_cache:
            return self.agents_cache
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_base}/api/agent/user/{self.user_id}",
                    headers=self._get_headers(),
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    agents = response.json()
                    self.agents_cache = agents
                    logger.info(f"Loaded {len(agents)} agents")
                    
                    self.company_agents_map = {}
                    for agent in agents:
                        company_id = agent.get("company_id")
                        if company_id:
                            if company_id not in self.company_agents_map:
                                self.company_agents_map[company_id] = []
                            self.company_agents_map[company_id].append(agent)
                    
                    return agents
                else:
                    logger.error(f"Failed to fetch agents: {response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error fetching agents: {str(e)}")
            return []
    
    async def get_company_info(self, company_id: str) -> Optional[Dict]:
        """Get company information"""
        companies = await self._load_companies()
        return next((c for c in companies if c["id"] == company_id), None)
    
    async def get_company_agents(self, company_id: str) -> List[Dict[str, str]]:
        """
        Get all active agents for a company, formatted for intent routing
        
        Returns:
        [
            {
                "agent_id": "uuid",
                "name": "Sales Agent",
                "description": "Handles sales inquiries",
                "type": "base",
                "prompt": "You are a sales agent..."
            },
            ...
        ]
        """
        try:
            if not self.agents_cache:
                await self._load_agents()
            
            company_agents = self.company_agents_map.get(company_id, [])
            
            formatted_agents = []
            for agent in company_agents:
                if agent.get("is_active", False):
                    # Build description from roleDescription or name
                    additional_context = agent.get("additional_context", {})
                    description = (
                        additional_context.get("roleDescription") or 
                        additional_context.get("businessContext") or
                        agent.get("name", "General agent")
                    )
                    
                    formatted_agents.append({
                        "agent_id": agent["id"],
                        "name": agent["name"],
                        "description": description,
                        "type": agent.get("type", "base"),
                        "prompt": agent.get("prompt", ""),
                        "tone": additional_context.get("tone", "professional"),
                        "language": additional_context.get("language", "english"),
                    })
            
            logger.info(f"Company {company_id}: {len(formatted_agents)} active agents")
            for agent in formatted_agents:
                logger.info(f"  - {agent['name']} ({agent['agent_id'][:8]}...)")
            
            return formatted_agents
            
        except Exception as e:
            logger.error(f"Error getting company agents: {str(e)}")
            return []
    
    async def get_agent_by_id(self, agent_id: str) -> Optional[Dict]:
        """Get specific agent by ID"""
        if not self.agents_cache:
            await self._load_agents()
        
        agent = next((a for a in self.agents_cache if a["id"] == agent_id), None)
        
        if agent:
            additional_context = agent.get("additional_context", {})
            description = (
                additional_context.get("roleDescription") or 
                additional_context.get("businessContext") or
                agent.get("name", "General agent")
            )
            
            return {
                "agent_id": agent["id"],
                "name": agent["name"],
                "description": description,
                "type": agent.get("type", "base"),
                "prompt": agent.get("prompt", ""),
                "tone": additional_context.get("tone", "professional"),
                "language": additional_context.get("language", "english"),
            }
        
        return None
    
    async def get_master_agent(self, company_id: str, master_agent_id: str) -> Optional[Dict]:
        """
        Get the master agent info
        Master agent is the one specified in the webhook URL
        """
        agent = await self.get_agent_by_id(master_agent_id)
        
        if agent:
            logger.info(f"Master Agent: {agent['name']} for company {company_id}")
        
        return agent
    
    async def refresh_cache(self):
        """Refresh the cache (call this periodically or on-demand)"""
        logger.info("Refreshing agent cache...")
        self.companies_cache = None
        self.agents_cache = None
        self.company_agents_map = {}
        await self._load_companies()
        await self._load_agents()
        logger.info("Cache refreshed")

# Global instance
agent_config_service = AgentConfigService()
