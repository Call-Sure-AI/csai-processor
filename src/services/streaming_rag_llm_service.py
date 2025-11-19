# src/services/streaming_rag_llm_service.py
"""
Streaming RAG + LLM Service
Combines RAG retrieval with streaming LLM responses for ultra-low latency

Key Features:
- Parallel RAG + LLM execution
- True streaming (word-by-word)
- Context compression
- Response caching
"""

import asyncio
import logging
import time
from typing import AsyncGenerator, List, Dict, Optional, Any
import json

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


class StreamingRAGLLM:
    """
    Streaming RAG + LLM service with parallel processing
    
    Optimizations:
    1. Parallel RAG retrieval while LLM is starting
    2. Context compression (only top 3 results)
    3. Streaming token generation
    4. Early response initiation
    """
    
    def __init__(self, rag_service, qdrant_service):
        self.rag_service = rag_service
        self.qdrant_service = qdrant_service
        
        # Performance tracking
        self.avg_rag_time = 0
        self.avg_llm_time = 0
        self.request_count = 0
        
        logger.info("🚀 StreamingRAGLLM initialized")
    
    async def stream_response(
        self,
        query: str,
        company_id: str,
        agent_id: str,
        conversation_history: Optional[List[Dict]] = None,
        call_sid: Optional[str] = None,
        use_rag: bool = True
    ) -> AsyncGenerator[str, None]:
        """
        Stream LLM response with RAG context
        
        Args:
            query: User query
            company_id: Company ID
            agent_id: Agent ID
            conversation_history: Previous turns
            call_sid: Call SID for function calling
            use_rag: Whether to use RAG (can disable for speed)
            
        Yields:
            Response text chunks
        """
        start_time = time.time()
        logger.info(f"🎯 Streaming response for: '{query[:50]}...'")
        
        try:
            # Start RAG task in parallel (don't await yet)
            rag_task = None
            if use_rag:
                logger.info("📚 Starting parallel RAG retrieval")
                rag_task = asyncio.create_task(
                    self._get_rag_context_fast(query, company_id, agent_id)
                )
            
            # Prepare initial messages while RAG is running
            messages = self._prepare_messages(
                query=query,
                conversation_history=conversation_history or []
            )
            
            # Try to get RAG context quickly (with timeout)
            rag_context = None
            if rag_task:
                try:
                    rag_context = await asyncio.wait_for(rag_task, timeout=0.5)
                    rag_time = (time.time() - start_time) * 1000
                    logger.info(f"📚 RAG completed in {rag_time:.2f}ms")
                    
                    # Update running average
                    self.avg_rag_time = (
                        (self.avg_rag_time * self.request_count + rag_time) /
                        (self.request_count + 1)
                    )
                    
                except asyncio.TimeoutError:
                    logger.warning("⚠️ RAG timeout (500ms) - continuing without context")
                    rag_context = None
            
            # Add RAG context to messages if available
            if rag_context:
                context_text = self._format_rag_context(rag_context)
                messages.insert(0, {
                    "role": "system",
                    "content": f"Use this context to answer:\n\n{context_text}"
                })
                logger.debug(f"📚 Added RAG context ({len(context_text)} chars)")
            
            # Stream LLM response
            llm_start = time.time()
            token_count = 0
            
            logger.info("💬 Starting LLM streaming")
            
            # Import LLM service
            from services.llm_service import llm_service
            
            # For OpenAI, use streaming
            if hasattr(llm_service, 'openai_api_key') and llm_service.openai_api_key:
                async for chunk in self._stream_openai(
                    messages=messages,
                    call_sid=call_sid
                ):
                    token_count += 1
                    yield chunk
            else:
                # Fallback: Generate full response
                response = await llm_service.generate_response(
                    messages=messages,
                    context={'company_id': company_id, 'agent_id': agent_id},
                    enable_functions=True,
                    call_sid=call_sid
                )
                yield response
            
            llm_time = (time.time() - llm_start) * 1000
            total_time = (time.time() - start_time) * 1000
            
            # Update metrics
            self.avg_llm_time = (
                (self.avg_llm_time * self.request_count + llm_time) /
                (self.request_count + 1)
            )
            self.request_count += 1
            
            logger.info(f"✅ Streaming completed in {total_time:.2f}ms ({token_count} tokens)")
            logger.info(f"📊 Avg RAG: {self.avg_rag_time:.0f}ms | Avg LLM: {self.avg_llm_time:.0f}ms")
            
        except Exception as e:
            logger.error(f"❌ Streaming error: {str(e)}", exc_info=True)
            yield "I apologize, but I encountered an error. Please try again."
    
    async def _get_rag_context_fast(
        self,
        query: str,
        company_id: str,
        agent_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Fast RAG retrieval with optimizations
        
        Optimizations:
        - Limit to top 3 results
        - No re-ranking
        - Compressed context
        """
        try:
            logger.debug(f"📚 RAG query: '{query[:30]}...'")
            
            # Generate embedding
            embedding = await self.rag_service.embeddings.aembed_query(query)
            
            # Search Qdrant (top 3 only)
            results = await self.qdrant_service.search(
                company_id=company_id,
                query_vector=embedding,
                agent_id=agent_id,
                limit=3  # Reduced for speed
            )
            
            logger.debug(f"📚 Found {len(results)} RAG results")
            
            return results
            
        except Exception as e:
            logger.error(f"❌ RAG retrieval error: {str(e)}")
            return None
    
    def _prepare_messages(
        self,
        query: str,
        conversation_history: List[Dict]
    ) -> List[Dict[str, str]]:
        """
        Prepare messages for LLM
        
        Optimizations:
        - Keep last 5 turns only
        - Compress long messages
        """
        messages = []
        
        # Keep last 5 turns
        recent_history = conversation_history[-10:] if len(conversation_history) > 10 else conversation_history
        
        for msg in recent_history:
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")[:500]  # Truncate long messages
            })
        
        # Add current query
        messages.append({
            "role": "user",
            "content": query
        })
        
        logger.debug(f"💬 Prepared {len(messages)} messages")
        
        return messages
    
    def _format_rag_context(self, rag_results: List[Dict[str, Any]]) -> str:
        """
        Format RAG context for LLM
        
        Optimizations:
        - Compress to ~300 tokens
        - Remove redundancy
        """
        if not rag_results:
            return ""
        
        context_parts = []
        total_length = 0
        max_length = 1200  # ~300 tokens
        
        for i, result in enumerate(rag_results, 1):
            content = result.get('content', '')
            
            # Truncate if needed
            if total_length + len(content) > max_length:
                remaining = max_length - total_length
                if remaining > 100:
                    content = content[:remaining] + "..."
                else:
                    break
            
            context_parts.append(f"[{i}] {content}")
            total_length += len(content)
        
        context = "\n\n".join(context_parts)
        logger.debug(f"📚 Formatted RAG context: {len(context)} chars")
        
        return context
    
    async def _stream_openai(
        self,
        messages: List[Dict],
        call_sid: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """
        Stream response from OpenAI
        """
        try:
            from openai import AsyncOpenAI
            from config.settings import settings
            
            client = AsyncOpenAI(api_key=settings.openai_api_key)
            
            # Get tools for function calling
            from functions.functions_mainfest import format_tools_for_openai
            tools = format_tools_for_openai()
            
            logger.debug("💬 Starting OpenAI streaming")
            
            # Stream response
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                max_tokens=settings.openai_max_tokens,
                temperature=settings.openai_temperature,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None,
                stream=True  # Enable streaming
            )
            
            # Stream tokens
            async for chunk in response:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    
                    if delta.content:
                        yield delta.content
                    
                    # Handle function calls (non-streaming)
                    if delta.tool_calls:
                        # For function calls, we need to collect all chunks first
                        # This is a limitation of streaming with function calls
                        logger.info("🔧 Function call detected - switching to non-streaming")
                        break
            
        except Exception as e:
            logger.error(f"❌ OpenAI streaming error: {str(e)}")
            raise
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get performance metrics"""
        return {
            'avg_rag_time_ms': self.avg_rag_time,
            'avg_llm_time_ms': self.avg_llm_time,
            'total_requests': self.request_count
        }


# Usage example
"""
# Initialize service
from services.rag.rag_service import get_rag_service
from services.vector_store.qdrant_service import qdrant_service

streaming_service = StreamingRAGLLM(
    rag_service=get_rag_service(),
    qdrant_service=qdrant_service
)

# Stream response
async for chunk in streaming_service.stream_response(
    query="What are your hours?",
    company_id="company_123",
    agent_id="agent_456",
    conversation_history=[],
    call_sid="CA_xyz"
):
    print(chunk, end='', flush=True)

# Check metrics
metrics = streaming_service.get_metrics()
print(f"\\nAvg RAG: {metrics['avg_rag_time_ms']:.0f}ms")
print(f"Avg LLM: {metrics['avg_llm_time_ms']:.0f}ms")
"""