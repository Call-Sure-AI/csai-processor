from typing import List, Dict, Optional, Any, AsyncIterator
import logging
import json
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from config.settings import settings
from services.agent_tools import TICKET_FUNCTIONS, execute_function

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
        
        self.qa_prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a helpful AI assistant for Callsure.ai customer support.

Use the following context from documents to answer questions accurately.

Context:
{context}

Instructions:
- Provide detailed, accurate responses based on the context
- If customer reports an issue, problem, or needs help, use the create_ticket function
- Be professional, friendly, and empathetic
- Keep responses concise but informative
- If you create a ticket, inform the customer and provide the ticket ID"""),
            ("human", "{question}")
        ])
    
    async def get_answer(
        self,
        company_id: str,
        question: str,
        agent_id: Optional[str] = None,
        call_sid: Optional[str] = None,
        conversation_context: Optional[List[Dict[str, str]]] = None
    ) -> AsyncIterator[str]:
        """Get streaming answer with function calling support"""
        try:
            logger.info(f"RAG Query: '{question[:50]}...'")
            
            # Generate embedding
            query_embedding = await self.embeddings.aembed_query(question)
            
            # Search documents
            search_results = await self.qdrant_service.search(
                company_id=company_id,
                query_vector=query_embedding,
                agent_id=agent_id,
                limit=5
            )
            
            # Build context
            if search_results:
                context_parts = []
                for idx, result in enumerate(search_results, 1):
                    doc_name = result.get("document_name", "Document")
                    content = result.get("content", "")
                    score = result.get("score", 0)
                    context_parts.append(f"[Source {idx}: {doc_name}]\n{content}\n")
                
                context = "\n".join(context_parts)
            else:
                context = "No specific documentation found."
            
            # Format prompt
            messages = self.qa_prompt.format_messages(
                context=context,
                question=question
            )

            response = await self.llm_with_functions.ainvoke(messages)
            
            # Check for function call
            if hasattr(response, 'additional_kwargs') and 'function_call' in response.additional_kwargs:
                function_call = response.additional_kwargs['function_call']
                function_name = function_call['name']
                arguments = json.loads(function_call['arguments'])
                
                logger.info(f"🔧 Function call: {function_name}")
                
                # Execute function
                function_result = await execute_function(
                    function_name=function_name,
                    arguments=arguments,
                    company_id=company_id,
                    call_sid=call_sid or "unknown"
                )
                
                yield function_result
            else:
                # Regular streaming response
                async for chunk in self.llm.astream(messages):
                    if chunk.content:
                        yield chunk.content
            
            logger.info("Response complete")
            
        except Exception as e:
            logger.error(f"RAG error: {str(e)}")
            yield "I encountered an error. Please try again."


# Global instance
rag_service = None

def get_rag_service():
    global rag_service
    if rag_service is None:
        from services.vector_store.qdrant_service import qdrant_service
        rag_service = RAGService(qdrant_service)
    return rag_service
