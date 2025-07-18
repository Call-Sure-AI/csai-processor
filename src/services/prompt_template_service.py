# src/services/prompt_template_service.py

from typing import Dict, List, Optional, Any, Union
import logging
import os
import asyncio
import time
import json
import yaml
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from database.models import PromptTemplate, Agent, AgentType, Company, PromptTemplateCategory
from datetime import datetime

logger = logging.getLogger(__name__)

class PromptTemplateService:
    def __init__(self, db_session: Session):
        self.db = db_session
        self.template_cache = {}  # Cache templates by id
        self.templates_by_type_cache = {}  # Cache templates by agent_type
        self.cache_ttl = 1800  # 30 minutes
        self.cache_timestamps = {}
        
        # Concurrency control
        self._cache_lock = asyncio.Lock()
        self._db_lock = asyncio.Lock()
        
        # Set up template directory
        self.template_dir = os.path.join(os.path.dirname(__file__), "../prompts")
        os.makedirs(self.template_dir, exist_ok=True)
        
        # Template configuration
        self.default_templates_file = os.path.join(self.template_dir, "default_templates.yaml")
        
    async def initialize(self):
        """Initialize the service by loading default templates"""
        try:
            # Check if we need to load default templates
            count = self.db.query(PromptTemplate).filter_by(is_system=True).count()
            if count == 0:
                await self._load_default_templates()
            logger.info(f"PromptTemplateService initialized with {count} system templates")
        except Exception as e:
            logger.error(f"Error initializing PromptTemplateService: {str(e)}")
    
    async def _load_default_templates(self):
        """Load default templates from YAML file"""
        try:
            async with self._db_lock:
                if not os.path.exists(self.default_templates_file):
                    await self._create_default_templates_file()
                
                with open(self.default_templates_file, 'r') as f:
                    templates = yaml.safe_load(f)
                
                for template_data in templates:
                    # Check if template already exists
                    existing = self.db.query(PromptTemplate).filter(
                        and_(
                            PromptTemplate.agent_type == template_data['agent_type'],
                            PromptTemplate.name == template_data['name'],
                            PromptTemplate.is_system == True
                        )
                    ).first()
                    
                    if not existing:
                        template = PromptTemplate(
                            name=template_data['name'],
                            description=template_data.get('description', ''),
                            content=template_data['content'],
                            category=template_data.get('category', PromptTemplateCategory.GENERAL),
                            agent_type=template_data['agent_type'],
                            variables=template_data.get('variables', []),
                            is_default=template_data.get('is_default', False),
                            is_system=True
                        )
                        self.db.add(template)
                
                self.db.commit()
                logger.info(f"Loaded {len(templates)} default templates")
                
        except Exception as e:
            logger.error(f"Error loading default templates: {str(e)}")
            self.db.rollback()
    
    async def _create_default_templates_file(self):
        """Create default templates file if it doesn't exist"""
        default_templates = [
            {
                "name": "Default Base Template",
                "description": "Default template for general-purpose agents",
                "content": """You are a helpful AI assistant.
                
                Provide clear, accurate, and helpful responses to user queries.
                Be concise but thorough in your explanations.
                
                Current date: {current_date}
                Company: {company_name}
                """,
                "category": PromptTemplateCategory.GENERAL,
                "agent_type": AgentType.base,
                "variables": ["current_date", "company_name"],
                "is_default": True
            },
            {
                "name": "Customer Support",
                "description": "Template for customer support agents",
                "content": """You are a customer support assistant for {company_name}.
                
                Provide friendly, helpful responses to customer inquiries.
                If you don't know an answer, politely say so and offer to connect them with a human agent.
                
                Guidelines:
                - Be empathetic and professional
                - Focus on solving the customer's problem
                - Provide step-by-step instructions when needed
                - Reference company policies accurately
                - Maintain a positive tone
                
                Current date: {current_date}
                """,
                "category": PromptTemplateCategory.CUSTOMER_SERVICE,
                "agent_type": AgentType.support,
                "variables": ["company_name", "current_date"],
                "is_default": True
            },
            {
                "name": "Booking Assistant",
                "description": "Template for booking and appointment scheduling agents",
                "content": """You are a booking assistant for {service_type} at {company_name}.
                
                Help customers book appointments, check availability, and manage their bookings.
                
                Guidelines:
                - Ask for necessary information to complete bookings (date, time, service)
                - Confirm availability before proceeding
                - Verify customer details for booking records
                - Explain cancellation and rescheduling policies
                - Send confirmation details
                
                Current date: {current_date}
                Working hours: {working_hours}
                """,
                "category": PromptTemplateCategory.BOOKING,
                "agent_type": AgentType.custom,
                "variables": ["service_type", "company_name", "current_date", "working_hours"],
                "is_default": False
            },
            {
                "name": "Sales Agent",
                "description": "Template for sales and product recommendation agents",
                "content": """You are a sales assistant for {company_name}, specializing in {product_category}.
                
                Help customers find the right products, answer questions about features, and guide them through the purchasing process.
                
                Guidelines:
                - Understand customer needs before recommending products
                - Highlight key features and benefits relevant to the customer
                - Provide accurate pricing and availability information
                - Explain warranty and return policies clearly
                - Assist with purchase decisions without being pushy
                
                Current date: {current_date}
                Current promotions: {promotions}
                """,
                "category": PromptTemplateCategory.SALES,
                "agent_type": AgentType.sales,
                "variables": ["company_name", "product_category", "current_date", "promotions"],
                "is_default": True
            },
            {
                "name": "Technical Support",
                "description": "Template for technical support and troubleshooting agents",
                "content": """You are a technical support specialist for {company_name}, with expertise in {technology_area}.
                
                Help users diagnose and resolve technical issues with our products and services.
                
                Guidelines:
                - Ask for specific error messages and symptoms
                - Guide users through troubleshooting steps clearly
                - Provide step-by-step instructions with proper formatting
                - Verify each step works before moving to the next
                - Escalate complex issues appropriately
                - Maintain a patient, reassuring tone
                
                Current date: {current_date}
                System version: {system_version}
                """,
                "category": PromptTemplateCategory.TECHNICAL,
                "agent_type": AgentType.technical,
                "variables": ["company_name", "technology_area", "current_date", "system_version"],
                "is_default": True
            }
        ]
        
        with open(self.default_templates_file, 'w') as f:
            yaml.dump(default_templates, f, sort_keys=False, indent=2)
            
        logger.info(f"Created default templates file: {self.default_templates_file}")
    
    async def get_template_by_id(self, template_id: str) -> Optional[PromptTemplate]:
        """Get a template by ID with caching"""
        try:
            # Check cache first
            if template_id in self.template_cache:
                # Check if cache is still valid
                if time.time() - self.cache_timestamps.get(template_id, 0) < self.cache_ttl:
                    return self.template_cache[template_id]
            
            # Query database
            template = self.db.query(PromptTemplate).filter_by(id=template_id).first()
            
            if template:
                # Update cache
                self.template_cache[template_id] = template
                self.cache_timestamps[template_id] = time.time()
                
            return template
            
        except Exception as e:
            logger.error(f"Error getting template by ID: {str(e)}")
            return None
    
    async def get_templates_by_agent_type(
        self, 
        agent_type: str,
        company_id: Optional[str] = None
    ) -> List[PromptTemplate]:
        """Get all templates for a specific agent type with caching"""
        try:
            cache_key = f"{agent_type}:{company_id or 'system'}"
            
            # Check cache first
            if cache_key in self.templates_by_type_cache:
                # Check if cache is still valid
                if time.time() - self.cache_timestamps.get(cache_key, 0) < self.cache_ttl:
                    return self.templates_by_type_cache[cache_key]
            
            # Query database
            query = self.db.query(PromptTemplate).filter(
                PromptTemplate.agent_type == agent_type
            )
            
            if company_id:
                # Include company-specific templates and system templates
                query = query.filter(
                    or_(
                        PromptTemplate.company_id == company_id,
                        PromptTemplate.is_system == True
                    )
                )
            else:
                # Only system templates
                query = query.filter(PromptTemplate.is_system == True)
                
            templates = query.all()
            
            # Update cache
            self.templates_by_type_cache[cache_key] = templates
            self.cache_timestamps[cache_key] = time.time()
            
            return templates
            
        except Exception as e:
            logger.error(f"Error getting templates by agent type: {str(e)}")
            return []
    
    async def get_default_template(
        self, 
        agent_type: str,
        company_id: Optional[str] = None
    ) -> Optional[PromptTemplate]:
        """Get the default template for an agent type"""
        try:
            # Query database
            query = self.db.query(PromptTemplate).filter(
                PromptTemplate.agent_type == agent_type,
                PromptTemplate.is_default == True
            )
            
            if company_id:
                # Try company-specific default first, then system default
                company_default = query.filter(PromptTemplate.company_id == company_id).first()
                if company_default:
                    return company_default
            
            # Get system default
            return query.filter(PromptTemplate.is_system == True).first()
            
        except Exception as e:
            logger.error(f"Error getting default template: {str(e)}")
            return None
    
    async def format_template(
        self, 
        template: Union[PromptTemplate, str],
        variables: Dict[str, Any]
    ) -> str:
        """Format a template with variables"""
        try:
            # Get template content
            if isinstance(template, PromptTemplate):
                template_content = template.content
            else:
                template_content = template
            
            # Format template
            try:
                return template_content.format(**variables)
            except KeyError as e:
                logger.warning(f"Missing variable in template: {str(e)}")
                # Use a safer approach that won't fail on missing variables
                for key, value in variables.items():
                    if value is not None:
                        placeholder = "{" + key + "}"
                        template_content = template_content.replace(placeholder, str(value))
                return template_content
                
        except Exception as e:
            logger.error(f"Error formatting template: {str(e)}")
            return template if isinstance(template, str) else template.content
    
    async def get_agent_prompt(
        self,
        agent_id: str,
        conversation_context: Optional[List[Dict]] = None,
        additional_variables: Optional[Dict[str, Any]] = None
    ) -> str:
        """Get formatted prompt for an agent with context"""
        try:
            # Get agent
            agent = self.db.query(Agent).filter_by(id=agent_id).first()
            if not agent:
                return "You are a helpful AI assistant."
            
            # Get template if specified
            template = None
            if agent.template_id:
                template = await self.get_template_by_id(agent.template_id)
            
            # If no template, get default for agent type
            if not template:
                template = await self.get_default_template(agent.type, agent.company_id)
            
            # If still no template, use agent's prompt directly
            if not template:
                template_content = agent.prompt
            else:
                template_content = template.content
            
            # Get company name
            company_name = "our company"
            if agent.company_id:
                company = self.db.query(Company).filter_by(id=agent.company_id).first()
                if company:
                    company_name = company.name
            
            # Prepare variables
            variables = {
                "agent_name": agent.name,
                "company_name": company_name,
                "current_date": datetime.utcnow().strftime("%Y-%m-%d")
            }
            
            # Add agent additional context if available
            if agent.additional_context and isinstance(agent.additional_context, dict):
                variables.update(agent.additional_context)
            
            # Add additional variables if provided
            if additional_variables:
                variables.update(additional_variables)
            
            # Add conversation context if available
            if conversation_context:
                # Format conversation context
                context_text = "\n".join([
                    f"{'User' if msg.get('role') == 'user' else 'Assistant'}: {msg.get('content', '')}"
                    for msg in conversation_context
                ])
                variables["conversation_context"] = context_text
            
            # Format template
            return await self.format_template(template_content, variables)
            
        except Exception as e:
            logger.error(f"Error getting agent prompt: {str(e)}")
            return "You are a helpful AI assistant."
    
    async def create_template(
        self,
        name: str,
        content: str,
        agent_type: str,
        company_id: Optional[str] = None,
        user_id: Optional[str] = None,
        description: Optional[str] = None,
        category: str = PromptTemplateCategory.GENERAL,
        variables: Optional[List[str]] = None,
        is_default: bool = False
    ) -> Optional[str]:
        """Create a new template"""
        async with self._db_lock:
            try:
                # If setting as default, unset current defaults
                if is_default and company_id:
                    current_defaults = self.db.query(PromptTemplate).filter(
                        PromptTemplate.agent_type == agent_type,
                        PromptTemplate.company_id == company_id,
                        PromptTemplate.is_default == True
                    ).all()
                    
                    for template in current_defaults:
                        template.is_default = False
                
                # Create new template
                template = PromptTemplate(
                    name=name,
                    content=content,
                    agent_type=agent_type,
                    company_id=company_id,
                    user_id=user_id,
                    description=description,
                    category=category,
                    variables=variables or [],
                    is_default=is_default,
                    is_system=False
                )
                
                self.db.add(template)
                self.db.commit()
                
                # Clear relevant caches
                async with self._cache_lock:
                    cache_key = f"{agent_type}:{company_id or 'system'}"
                    if cache_key in self.templates_by_type_cache:
                        del self.templates_by_type_cache[cache_key]
                
                return template.id
                
            except Exception as e:
                logger.error(f"Error creating template: {str(e)}")
                self.db.rollback()
                return None
    
    async def update_template(
        self,
        template_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """Update an existing template"""
        async with self._db_lock:
            try:
                template = self.db.query(PromptTemplate).filter_by(id=template_id).first()
                if not template:
                    return False
                
                # Don't update system templates
                if template.is_system:
                    logger.warning(f"Cannot update system template: {template_id}")
                    return False
                
                # If setting as default, unset current defaults
                if updates.get('is_default') and template.company_id:
                    current_defaults = self.db.query(PromptTemplate).filter(
                        PromptTemplate.agent_type == template.agent_type,
                        PromptTemplate.company_id == template.company_id,
                        PromptTemplate.is_default == True,
                        PromptTemplate.id != template_id
                    ).all()
                    
                    for other_template in current_defaults:
                        other_template.is_default = False
                
                # Update fields
                valid_fields = ['name', 'content', 'description', 'category', 'variables', 'is_default']
                for field, value in updates.items():
                    if field in valid_fields:
                        setattr(template, field, value)
                
                template.updated_at = datetime.now()
                self.db.commit()
                
                # Clear caches
                async with self._cache_lock:
                    if template_id in self.template_cache:
                        del self.template_cache[template_id]
                    
                    cache_key = f"{template.agent_type}:{template.company_id or 'system'}"
                    if cache_key in self.templates_by_type_cache:
                        del self.templates_by_type_cache[cache_key]
                
                return True
                
            except Exception as e:
                logger.error(f"Error updating template: {str(e)}")
                self.db.rollback()
                return False
    
    async def delete_template(self, template_id: str) -> bool:
        """Delete a template"""
        async with self._db_lock:
            try:
                template = self.db.query(PromptTemplate).filter_by(id=template_id).first()
                if not template:
                    return False
                
                # Don't delete system templates
                if template.is_system:
                    logger.warning(f"Cannot delete system template: {template_id}")
                    return False
                
                # Update any agents using this template
                agents_using_template = self.db.query(Agent).filter_by(template_id=template_id).all()
                for agent in agents_using_template:
                    # Get default template content
                    default_template = await self.get_default_template(agent.type, agent.company_id)
                    if default_template:
                        agent.prompt = default_template.content
                    agent.template_id = None
                
                # Delete template
                self.db.delete(template)
                self.db.commit()
                
                # Clear caches
                async with self._cache_lock:
                    if template_id in self.template_cache:
                        del self.template_cache[template_id]
                    
                    cache_key = f"{template.agent_type}:{template.company_id or 'system'}"
                    if cache_key in self.templates_by_type_cache:
                        del self.templates_by_type_cache[cache_key]
                
                return True
                
            except Exception as e:
                logger.error(f"Error deleting template: {str(e)}")
                self.db.rollback()
                return False
    
    async def assign_template_to_agent(
        self,
        agent_id: str,
        template_id: str
    ) -> bool:
        """Assign a template to an agent"""
        async with self._db_lock:
            try:
                agent = self.db.query(Agent).filter_by(id=agent_id).first()
                if not agent:
                    return False
                
                template = self.db.query(PromptTemplate).filter_by(id=template_id).first()
                if not template:
                    return False
                
                # Make sure template is compatible with agent type
                if template.agent_type != agent.type and template.agent_type != AgentType.base:
                    logger.warning(f"Template {template_id} not compatible with agent type {agent.type}")
                    return False
                
                # Update agent
                agent.template_id = template_id
                agent.updated_at = datetime.now()
                self.db.commit()
                
                return True
                
            except Exception as e:
                logger.error(f"Error assigning template to agent: {str(e)}")
                self.db.rollback()
                return False