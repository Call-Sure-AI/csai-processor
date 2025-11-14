from typing import List, Dict, Optional, Any, AsyncIterator
import logging
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_classic.chains import RetrievalQA
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import AIMessageChunk
import asyncio
import json
from config.settings import settings

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self, qdrant_service):
        """Initialize RAG service with necessary components"""
        self.qdrant_service = qdrant_service
        
        # Initialize OpenAI LLM
        self.llm = ChatOpenAI(
            model_name=settings.openai_model,
            temperature=0.1,
            openai_api_key=settings.openai_api_key
        )
        
        # Custom QA prompt template
        self.qa_template = """You are a helpful AI assistant for a customer support line. Use the following pieces of context to answer the question.
If the context contains relevant information, provide a detailed and accurate response.
If the context doesn't contain relevant information, respond in a helpful, friendly way without mentioning the lack of context.

Context: {context}

Question: {input}

Instructions:
1. Base your answer ONLY on the provided context when relevant
2. Be specific and cite information from the context when possible
3. If the context is not relevant, provide a general helpful response as a customer service agent
4. Maintain a helpful and friendly conversational tone
5. Keep responses concise and to the point
6. If the user is simply saying hello, greet them warmly and ask how you can help

Answer: """
        
        # Caching mechanism for chains
        self.chain_cache = {}
    
    async def create_qa_chain(self, company_id: str, agent_id: Optional[str] = None, agent_prompt: Optional[str] = None):
        """Create a QA chain using the vector store for a company/agent"""
        try:
            # Check if chain already exists in cache
            cache_key = f"{company_id}_{agent_id or 'all'}"
            if cache_key in self.chain_cache:
                return self.chain_cache[cache_key]
            
            # Get vector store
            vector_store = await self.qdrant_service.get_vector_store(company_id)
            
            # Create retriever, optionally with agent filter
            search_kwargs = {"k": 5, "score_threshold": 0.2}
            if agent_id:
                from qdrant_client import models
                search_filter = models.Filter(
                    must=[models.FieldCondition(
                        key="metadata.agent_id",
                        match=models.MatchValue(value=str(agent_id))
                    )]
                )
                search_kwargs["filter"] = search_filter
            
            retriever = vector_store.as_retriever(
                search_type="similarity",
                search_kwargs=search_kwargs
            )
            
            # Use the agent_prompt if provided, otherwise use the default template
            qa_template = self.qa_template
            if agent_prompt:
                # Add necessary placeholders to the agent's prompt
                if "{context}" not in agent_prompt:
                    agent_prompt = f"{agent_prompt}\n\nContext: {{context}}\n\nQuestion: {{input}}\n\nAnswer: "
                qa_template = agent_prompt
            
            # Create prompt using ChatPromptTemplate
            qa_prompt = ChatPromptTemplate.from_template(qa_template)
            
            # Build the new-style chain
            combine_docs_chain = create_stuff_documents_chain(self.llm, qa_prompt)
            chain = create_retrieval_chain(retriever, combine_docs_chain)
            
            # Cache the chain
            self.chain_cache[cache_key] = chain
            
            logger.info(f"Created QA chain for company {company_id}, agent {agent_id}")
            return chain
            
        except Exception as e:
            logger.error(f"Error creating QA chain: {str(e)}")
            raise
    
    async def get_answer_with_chain(
        self,
        chain,
        question: str,
        conversation_context: Optional[List[Dict]] = None,
        company_name: str = "our service"
    ) -> AsyncIterator[str]:
        """Stream responses token-by-token from the RAG chain"""
        try:
            logger.info(f"Querying chain with question: {question}")
            
            # Handle special system commands
            if question == "__SYSTEM_WELCOME__":
                welcome_message = f"Hello! Welcome to {company_name}. I'm your AI voice assistant. How may I help you today?"
                for token in welcome_message.split():
                    yield token + " "
                return
                
            # Add conversation context if available
            query_with_context = question
            if conversation_context:
                recent_messages = "\n".join([
                    f"{'User' if msg['role'] == 'user' else 'Assistant'}: {msg['content']}"
                    for msg in conversation_context[-3:]
                ])
                query_with_context = f"{recent_messages}\n\nCurrent question: {question}"
                logger.info(f"Added conversation context: {recent_messages[:100]}...")
            
            # Stream response using astream_events
            async for event in chain.astream_events(
                {"input": query_with_context},  # Changed from "query" and "question" to just "input"
                config=RunnableConfig(run_name="streaming_chain"),
                version="v2"
            ):
                if event["event"] == "on_chat_model_stream":
                    chunk = event["data"].get("chunk", "")
                    if isinstance(chunk, AIMessageChunk):
                        token = chunk.content
                    else:
                        token = str(chunk)
                    
                    if token:
                        yield token
                
        except Exception as e:
            logger.error(f"Error getting answer with chain: {str(e)}")
            yield "I encountered an error processing your question."
    
    async def get_answer(
        self,
        company_id: str,
        question: str,
        agent_id: Optional[str] = None,
        conversation_context: Optional[List[Dict]] = None
    ) -> AsyncIterator[str]:
        """Get answer to a question using RAG"""
        try:
            # Check if embeddings exist
            has_embeddings = await self.qdrant_service.verify_embeddings(company_id, agent_id)
            if not has_embeddings:
                yield "I don't have any knowledge documents to reference. Please add some documents first."
                return
            
            # Create chain
            chain = await self.create_qa_chain(company_id, agent_id)
            
            # Get streaming answer
            async for token in self.get_answer_with_chain(chain, question, conversation_context):
                yield token
                
        except Exception as e:
            logger.error(f"Error getting answer: {str(e)}")
            yield "I encountered an error processing your question."
