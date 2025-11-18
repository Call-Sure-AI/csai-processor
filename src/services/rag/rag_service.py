from typing import List, Dict, Optional, Any, AsyncIterator
import logging
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from config.settings import settings

logger = logging.getLogger(__name__)

class RAGService:
    def __init__(self, qdrant_service):
        """Initialize RAG with OpenAI API"""
        self.qdrant_service = qdrant_service
        
        self.llm = ChatOpenAI(
            model=settings.openai_model or "gpt-4o-mini",
            temperature=0.3,
            openai_api_key=settings.openai_api_key,
            streaming=True
        )
        
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
- If context doesn't have enough info, say so politely
- Be professional and friendly
- Keep responses concise but informative"""),
            ("human", "{question}")
        ])
    
    async def get_answer(
        self,
        company_id: str,
        question: str,
        agent_id: Optional[str] = None,
        conversation_context: Optional[List[Dict[str, str]]] = None,
        document_type: Optional[str] = None
    ) -> AsyncIterator[str]:
        """Get streaming answer using RAG"""
        try:
            logger.info(f"Generating embedding for: {question[:50]}...")
            query_embedding = await self.embeddings.aembed_query(question)
            
            logger.info(f"Searching docs for company {company_id}, agent {agent_id}")
            search_results = await self.qdrant_service.search(
                company_id=company_id,
                query_vector=query_embedding,
                agent_id=agent_id,
                document_type=document_type,
                limit=5
            )
            
            if not search_results:
                yield "I don't have relevant information in my knowledge base for that question. How else can I help you?"
                return
            
            context_parts = []
            for idx, result in enumerate(search_results, 1):
                doc_name = result.get("document_name", "Document")
                content = result.get("content", "")
                score = result.get("score", 0)
                
                context_parts.append(
                    f"[Source {idx}: {doc_name} (Relevance: {score:.2f})]\n{content}\n"
                )
            
            context = "\n".join(context_parts)
            logger.info(f"Using {len(search_results)} documents")
            
            messages = self.qa_prompt.format_messages(
                context=context,
                question=question
            )
            
            logger.info("Generating response via OpenAI...")
            async for chunk in self.llm.astream(messages):
                if chunk.content:
                    yield chunk.content
            
            logger.info("Response complete")
            
        except Exception as e:
            logger.error(f"RAG error: {str(e)}")
            yield "I encountered an error. Please try again."
    
    async def get_answer_sync(
        self,
        company_id: str,
        question: str,
        agent_id: Optional[str] = None,
        document_type: Optional[str] = None
    ) -> str:
        """Get non-streaming answer"""
        try:
            response_parts = []
            async for chunk in self.get_answer(
                company_id=company_id,
                question=question,
                agent_id=agent_id,
                document_type=document_type
            ):
                response_parts.append(chunk)
            
            return "".join(response_parts)
            
        except Exception as e:
            logger.error(f"RAG error: {str(e)}")
            return "I encountered an error. Please try again."


# Global instance
rag_service = None

def get_rag_service():
    global rag_service
    if rag_service is None:
        from services.vector_store.qdrant_service import qdrant_service
        rag_service = RAGService(qdrant_service)
    return rag_service
