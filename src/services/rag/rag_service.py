# src/services/rag/rag_service.py
from typing import List, Dict, Optional, Any, AsyncIterator, AsyncGenerator
import logging
import json
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from config.settings import settings
from services.agent_tools import TICKET_FUNCTIONS, execute_function
from services.agent_config_service import agent_config_service
from services.prompt_template_service import prompt_template_service

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self, qdrant_service):
        """Initialize RAG with function calling support"""
        self.qdrant_service = qdrant_service
        
        self.llm = ChatOpenAI(
            model=settings.openai_model or "gpt-4o-mini",
            temperature=0.3,
            openai_api_key=settings.openai_api_key,
            streaming=True
        )

        self.llm_with_functions = ChatOpenAI(
            model=settings.openai_model or "gpt-4o-mini",
            temperature=0.3,
            openai_api_key=settings.openai_api_key
        ).bind(functions=TICKET_FUNCTIONS)
        
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            openai_api_key=settings.openai_api_key
        )
    
    def _build_dynamic_system_prompt(self, agent_config: Dict, context: str) -> str:
        """
        Build dynamic system prompt from agent configuration
        WITHOUT hardcoding company names
        """
        # Extract agent details
        agent_name = agent_config.get('name', 'Assistant')
        base_prompt = agent_config.get('prompt', '')
        
        # Extract additional context
        additional_context = agent_config.get('additional_context', {})
        tone = additional_context.get('tone', 'professional')
        language = additional_context.get('language', 'english')
        business_context = additional_context.get('businessContext', '')
        role_description = additional_context.get('roleDescription', '')
        
        # Extract response settings
        max_tokens = agent_config.get('max_response_tokens', 300)
        
        # Build dynamic prompt
        system_prompt = f"""You are {agent_name}, a {tone} customer service representative on a live phone call.

**Your Role**: {role_description if role_description else 'Provide helpful customer support'}

**Business Context**: 
{business_context if business_context else 'General customer service'}

**Available Information**:
{context}

**Communication Guidelines**:
- Tone: {tone.title()}
- Language: {language.title()}
- Keep responses conversational and under {max_tokens // 4} words
- Be empathetic, clear, and solution-focused
- Listen actively and acknowledge concerns

**Your Instructions**:
{base_prompt}

**Function Calling**:
- If customer reports an issue, problem, or needs help, use the create_ticket function
- After creating a ticket, inform the customer and provide the ticket ID

**Important Rules**:
- Never mention competitor companies by name
- Focus on the customer's specific needs
- Use the provided context to give accurate information
- If uncertain, be honest and offer to escalate
- Respond naturally as in a phone conversation"""

        return system_prompt.strip()
    
    async def get_answer(
        self,
        company_id: str,
        question: str,
        agent_id: Optional[str] = None,
        call_sid: Optional[str] = None,
        conversation_context: Optional[List[Dict[str, str]]] = None
    ) -> AsyncIterator[str]:
        """Get streaming answer with dynamic prompts and function calling"""
        try:
            logger.info(f"RAG Query: '{question[:50]}...'")            
            agent_config = await agent_config_service.get_agent_by_id(agent_id)
            if not agent_config:
                logger.error(f"Agent {agent_id[:8] if agent_id else 'None'}... not found")
                yield "I'm having trouble accessing my configuration. Please try again."
                return
            
            logger.info(f"Using agent: {agent_config.get('name')} ({agent_id[:8]}...)")

            query_embedding = await self.embeddings.aembed_query(question)
            
            search_results = await self.qdrant_service.search(
                company_id=company_id,
                query_vector=query_embedding,
                agent_id=agent_id,
                limit=5
            )

            if search_results:
                logger.info(f"Found {len(search_results)} relevant documents")
                context_parts = []
                for idx, result in enumerate(search_results, 1):
                    doc_name = result.get("document_name", "Document")
                    content = result.get("content", "")
                    score = result.get("score", 0)
                    context_parts.append(
                        f"[Source {idx}: {doc_name} (relevance: {score:.2f})]\n{content}\n"
                    )
                context = "\n".join(context_parts)
            else:
                logger.warning(f"No documents found for agent {agent_id[:8]}...")
                if conversation_context and len(conversation_context) > 0:
                    logger.info("Using conversation history as context")
                    
                    # Extract recent conversation summary
                    recent_messages = conversation_context[-6:]
                    conversation_summary = "\n".join([
                        f"{msg.get('role', 'unknown').upper()}: {msg.get('content', '')}"
                        for msg in recent_messages
                    ])
                    
                    context = f"""**Previous Conversation Context**:
    {conversation_summary}

    **Instructions**: 
    - Use the conversation history above to maintain context
    - Reference what was discussed earlier in the conversation
    - Don't ask for information already provided
    - Continue the conversation naturally based on what the customer has told you
    - If you need clarification, ask specific follow-up questions"""
                else:
                    context = """**No Documentation Available**

    **Instructions**:
    - Use general knowledge about the business context provided in your role
    - Ask relevant questions to gather information needed
    - Be helpful and guide the conversation toward booking or support
    - Acknowledge what you can and cannot help with
    - Offer to escalate if needed"""


            system_prompt = self._build_dynamic_system_prompt(agent_config, context)

            messages = [
                {"role": "system", "content": system_prompt}
            ]
            
            # Add conversation history if available
            if conversation_context:
                messages.extend(conversation_context[-10:])  # Last 10 messages
            
            # Add current question
            messages.append({"role": "user", "content": question})
            logger.info(f"Added {len(conversation_context[-10:])} messages from history")

            response = await self.llm_with_functions.ainvoke(messages)
            
            # Check for function call
            if hasattr(response, 'additional_kwargs') and 'function_call' in response.additional_kwargs:
                function_call = response.additional_kwargs['function_call']
                function_name = function_call['name']
                arguments = json.loads(function_call['arguments'])
                
                logger.info(f"Function call: {function_name} with args: {arguments}")

                campaign_id = None
                if conversation_context:
                    for msg in conversation_context:
                        if msg.get('role') == 'system' and 'campaign_id' in msg.get('content', ''):
                            import re
                            match = re.search(r'campaign_id[:\s]+([A-Z0-9-]+)', msg['content'])
                            if match:
                                campaign_id = match.group(1)
                                break
                                            
                # Execute function
                function_result = await execute_function(
                    function_name=function_name,
                    arguments=arguments,
                    company_id=company_id,
                    call_sid=call_sid or "unknown",
                    campaign_id=campaign_id
                )
                
                yield function_result
            else:
                # Regular streaming response
                logger.info(f"Streaming response...")
                async for chunk in self.llm.astream(messages):
                    if chunk.content:
                        yield chunk.content
            
            logger.info("Response complete")
            
        except Exception as e:
            logger.error(f"RAG error: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            yield "I apologize, I'm having trouble right now. Could you please repeat your question?"

    async def get_answer_with_acknowledgment(
        self,
        company_id: str,
        question: str,
        agent_id: str,
        call_sid: str = None,
        agent: Dict = None
    ) -> tuple[str, AsyncGenerator]:
        """
        Get answer with intelligent acknowledgment before RAG query
        Returns: (acknowledgment_message, answer_generator)
        """

        acknowledgment = prompt_template_service.generate_rag_acknowledgment(question, agent)

        answer_gen = self.get_answer(
            company_id=company_id,
            question=question,
            agent_id=agent_id,
            call_sid=call_sid
        )
        
        return (acknowledgment, answer_gen)

# Global instance
rag_service = None


def get_rag_service():
    """Get or create RAG service instance"""
    global rag_service
    if rag_service is None:
        from services.vector_store.qdrant_service import qdrant_service
        rag_service = RAGService(qdrant_service)
    return rag_service
