# src/services/cache/redis_cache_layer.py
"""
Redis Cache Layer for Voice AI Responses

Intelligent caching strategy:
- Common queries (e.g., "business hours", "location")
- Recent responses (TTL-based)
- User-specific context
- Conversation summaries

Performance Impact:
- Cache hit: <5ms (vs 2000ms full pipeline)
- Cache miss: Full pipeline + cache storage
- Expected hit rate: 30-40% for typical use cases
"""

import logging
import time
import hashlib
import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from services.redis_service import get_redis_service

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


class CacheStrategy:
    """Define caching strategies for different query types"""
    
    # Query patterns that should be cached longer
    LONG_CACHE_PATTERNS = [
        'business hours', 'opening hours', 'when open', 'hours of operation',
        'location', 'address', 'where located', 'find you',
        'phone number', 'contact', 'email',
        'about', 'what do you do', 'services',
        'pricing', 'how much', 'cost',
    ]
    
    # Query patterns that should be cached short-term
    SHORT_CACHE_PATTERNS = [
        'current', 'today', 'now', 'latest',
        'status', 'available', 'in stock',
    ]
    
    # Queries that should NOT be cached
    NO_CACHE_PATTERNS = [
        'my account', 'my order', 'personal',
        'transfer', 'speak to', 'human',
        'cancel', 'refund',
    ]
    
    @staticmethod
    def get_ttl(query: str) -> int:
        """
        Determine TTL based on query content
        
        Returns:
            TTL in seconds (0 = no cache)
        """
        query_lower = query.lower()
        
        # Check no-cache patterns
        if any(pattern in query_lower for pattern in CacheStrategy.NO_CACHE_PATTERNS):
            logger.debug(f"🚫 No cache for: '{query[:30]}...'")
            return 0
        
        # Check long-cache patterns
        if any(pattern in query_lower for pattern in CacheStrategy.LONG_CACHE_PATTERNS):
            logger.debug(f"⏰ Long cache (1 hour) for: '{query[:30]}...'")
            return 3600  # 1 hour
        
        # Check short-cache patterns
        if any(pattern in query_lower for pattern in CacheStrategy.SHORT_CACHE_PATTERNS):
            logger.debug(f"⏰ Short cache (5 min) for: '{query[:30]}...'")
            return 300  # 5 minutes
        
        # Default: medium cache
        logger.debug(f"⏰ Medium cache (15 min) for: '{query[:30]}...'")
        return 900  # 15 minutes


class RedisCacheLayer:
    """
    Redis-based cache layer for voice AI responses
    
    Features:
    - Intelligent TTL based on query type
    - Context-aware caching
    - Conversation history caching
    - Performance metrics
    """
    
    def __init__(self):
        """Initialize Redis cache layer"""
        self.redis = get_redis_service()
        
        # Cache prefixes
        self.RESPONSE_PREFIX = "voice_response"
        self.CONVERSATION_PREFIX = "conversation_history"
        self.CONTEXT_PREFIX = "rag_context"
        self.METRICS_PREFIX = "cache_metrics"
        
        # Performance tracking
        self.hits = 0
        self.misses = 0
        self.stores = 0
        
        logger.info("🚀 RedisCacheLayer initialized")
        logger.info(f"📍 Redis URL: {self.redis.redis_url}")
    
    def _generate_cache_key(
        self,
        prefix: str,
        company_id: str,
        query_or_id: str,
        agent_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> str:
        """
        Generate cache key with consistent hashing
        
        Args:
            prefix: Cache key prefix
            company_id: Company ID
            query_or_id: Query text or identifier
            agent_id: Optional agent ID
            user_id: Optional user ID for personalization
            
        Returns:
            Cache key
        """
        # Normalize query
        query_normalized = query_or_id.lower().strip()
        
        # Create key components
        components = [
            prefix,
            company_id,
            agent_id or "default",
        ]
        
        # Add user context if available (for personalized responses)
        if user_id:
            components.append(user_id)
        
        # Hash the query for consistent key length
        query_hash = hashlib.md5(query_normalized.encode()).hexdigest()
        components.append(query_hash)
        
        key = ":".join(components)
        logger.debug(f"🔑 Generated key: {key[:50]}...")
        
        return key
    
    async def get_cached_response(
        self,
        query: str,
        company_id: str,
        agent_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached response for a query
        
        Args:
            query: User query
            company_id: Company ID
            agent_id: Optional agent ID
            user_id: Optional user ID
            
        Returns:
            Cached response data or None
        """
        start_time = time.time()
        
        try:
            key = self._generate_cache_key(
                self.RESPONSE_PREFIX,
                company_id,
                query,
                agent_id,
                user_id
            )
            
            cached_data = self.redis.get(key)
            
            if cached_data:
                self.hits += 1
                retrieval_time = (time.time() - start_time) * 1000
                
                logger.info(f"💾 Cache HIT: '{query[:30]}...' in {retrieval_time:.2f}ms")
                logger.info(f"📊 Cache stats: {self.hits} hits, {self.misses} misses ({self.get_hit_rate():.1f}%)")
                
                # Update metrics
                self._update_metrics('hit')
                
                return cached_data
            else:
                self.misses += 1
                logger.info(f"❌ Cache MISS: '{query[:30]}...'")
                logger.info(f"📊 Cache stats: {self.hits} hits, {self.misses} misses ({self.get_hit_rate():.1f}%)")
                
                # Update metrics
                self._update_metrics('miss')
                
                return None
                
        except Exception as e:
            logger.error(f"❌ Cache retrieval error: {str(e)}")
            return None
    
    async def cache_response(
        self,
        query: str,
        response: str,
        company_id: str,
        agent_id: Optional[str] = None,
        user_id: Optional[str] = None,
        rag_context: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Cache a response
        
        Args:
            query: User query
            response: AI response
            company_id: Company ID
            agent_id: Optional agent ID
            user_id: Optional user ID
            rag_context: Optional RAG context used
            metadata: Optional additional metadata
            
        Returns:
            Success status
        """
        try:
            # Determine TTL based on query
            ttl = CacheStrategy.get_ttl(query)
            
            if ttl == 0:
                logger.info(f"🚫 Skipping cache for: '{query[:30]}...'")
                return False
            
            key = self._generate_cache_key(
                self.RESPONSE_PREFIX,
                company_id,
                query,
                agent_id,
                user_id
            )
            
            # Prepare cache data
            cache_data = {
                'query': query,
                'response': response,
                'company_id': company_id,
                'agent_id': agent_id,
                'user_id': user_id,
                'rag_context': rag_context,
                'metadata': metadata or {},
                'cached_at': datetime.utcnow().isoformat(),
                'ttl': ttl
            }
            
            # Store in Redis
            success = self.redis.set(key, cache_data, expire=ttl)
            
            if success:
                self.stores += 1
                logger.info(f"💾 Cached response: '{query[:30]}...' (TTL={ttl}s)")
                logger.debug(f"📦 Cache data size: {len(json.dumps(cache_data))} bytes")
                
                # Update metrics
                self._update_metrics('store')
                
                return True
            else:
                logger.error(f"❌ Failed to cache response")
                return False
                
        except Exception as e:
            logger.error(f"❌ Cache storage error: {str(e)}")
            return False
    
    async def get_conversation_history(
        self,
        conversation_id: str,
        limit: int = 10
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached conversation history
        
        Args:
            conversation_id: Conversation ID
            limit: Maximum number of turns to retrieve
            
        Returns:
            List of conversation turns or None
        """
        try:
            key = f"{self.CONVERSATION_PREFIX}:{conversation_id}"
            
            # Get from Redis list
            history = self.redis.lrange(key, -limit, -1)
            
            if history:
                logger.debug(f"💬 Retrieved {len(history)} conversation turns")
                return history
            else:
                logger.debug(f"💬 No cached conversation history")
                return None
                
        except Exception as e:
            logger.error(f"❌ Conversation history retrieval error: {str(e)}")
            return None
    
    async def add_conversation_turn(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        max_history: int = 50
    ) -> bool:
        """
        Add a turn to conversation history
        
        Args:
            conversation_id: Conversation ID
            role: Role (user/assistant)
            content: Message content
            metadata: Optional metadata
            max_history: Maximum turns to keep
            
        Returns:
            Success status
        """
        try:
            key = f"{self.CONVERSATION_PREFIX}:{conversation_id}"
            
            # Prepare turn data
            turn_data = {
                'role': role,
                'content': content,
                'metadata': metadata or {},
                'timestamp': datetime.utcnow().isoformat()
            }
            
            # Add to Redis list
            self.redis.rpush(key, turn_data)
            
            # Trim to max history
            current_length = self.redis.client.llen(key)
            if current_length > max_history:
                # Remove oldest entries
                self.redis.client.ltrim(key, -max_history, -1)
                logger.debug(f"💬 Trimmed conversation to {max_history} turns")
            
            # Set expiration (24 hours)
            self.redis.expire(key, 86400)
            
            logger.debug(f"💬 Added {role} turn to conversation {conversation_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Conversation turn storage error: {str(e)}")
            return False
    
    async def cache_rag_context(
        self,
        query: str,
        company_id: str,
        agent_id: str,
        context: List[Dict[str, Any]],
        ttl: int = 300
    ) -> bool:
        """
        Cache RAG context for reuse
        
        Args:
            query: Query text
            company_id: Company ID
            agent_id: Agent ID
            context: RAG context results
            ttl: Time to live in seconds
            
        Returns:
            Success status
        """
        try:
            key = self._generate_cache_key(
                self.CONTEXT_PREFIX,
                company_id,
                query,
                agent_id
            )
            
            cache_data = {
                'query': query,
                'context': context,
                'cached_at': datetime.utcnow().isoformat()
            }
            
            success = self.redis.set(key, cache_data, expire=ttl)
            
            if success:
                logger.debug(f"📚 Cached RAG context for: '{query[:30]}...'")
                return True
            else:
                return False
                
        except Exception as e:
            logger.error(f"❌ RAG context cache error: {str(e)}")
            return False
    
    async def get_rag_context(
        self,
        query: str,
        company_id: str,
        agent_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached RAG context
        
        Args:
            query: Query text
            company_id: Company ID
            agent_id: Agent ID
            
        Returns:
            Cached RAG context or None
        """
        try:
            key = self._generate_cache_key(
                self.CONTEXT_PREFIX,
                company_id,
                query,
                agent_id
            )
            
            cached_data = self.redis.get(key)
            
            if cached_data:
                logger.debug(f"📚 Retrieved cached RAG context")
                return cached_data.get('context')
            else:
                return None
                
        except Exception as e:
            logger.error(f"❌ RAG context retrieval error: {str(e)}")
            return None
    
    def _update_metrics(self, metric_type: str):
        """Update cache metrics in Redis"""
        try:
            date_key = datetime.utcnow().strftime("%Y-%m-%d")
            key = f"{self.METRICS_PREFIX}:{date_key}"
            
            # Increment counter
            self.redis.client.hincrby(key, metric_type, 1)
            
            # Set expiration (7 days)
            self.redis.expire(key, 604800)
            
        except Exception as e:
            logger.error(f"❌ Metrics update error: {str(e)}")
    
    def get_hit_rate(self) -> float:
        """Get cache hit rate percentage"""
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return (self.hits / total) * 100
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get cache performance metrics"""
        hit_rate = self.get_hit_rate()
        
        return {
            'hits': self.hits,
            'misses': self.misses,
            'stores': self.stores,
            'total_requests': self.hits + self.misses,
            'hit_rate_percent': round(hit_rate, 2),
            'efficiency_score': round(hit_rate / 100 * (1 - self.misses / max(self.hits + self.misses, 1)), 2)
        }
    
    def clear_cache(self, pattern: Optional[str] = None):
        """
        Clear cache entries
        
        Args:
            pattern: Optional pattern to match (e.g., "voice_response:company_123:*")
        """
        try:
            if pattern:
                keys = self.redis.keys(pattern)
                for key in keys:
                    self.redis.delete(key)
                logger.info(f"🗑️ Cleared {len(keys)} cache entries matching pattern: {pattern}")
            else:
                # Clear all voice-related caches
                for prefix in [self.RESPONSE_PREFIX, self.CONVERSATION_PREFIX, self.CONTEXT_PREFIX]:
                    keys = self.redis.keys(f"{prefix}:*")
                    for key in keys:
                        self.redis.delete(key)
                    logger.info(f"🗑️ Cleared {len(keys)} entries for prefix: {prefix}")
            
            # Reset metrics
            self.hits = 0
            self.misses = 0
            self.stores = 0
            
        except Exception as e:
            logger.error(f"❌ Cache clear error: {str(e)}")
    
    def warm_cache(self, common_queries: List[Dict[str, Any]]):
        """
        Pre-warm cache with common queries
        
        Args:
            common_queries: List of dicts with 'query', 'response', 'company_id', 'agent_id'
        """
        logger.info(f"🔥 Warming cache with {len(common_queries)} common queries")
        
        warmed = 0
        for query_data in common_queries:
            try:
                asyncio.create_task(self.cache_response(
                    query=query_data['query'],
                    response=query_data['response'],
                    company_id=query_data['company_id'],
                    agent_id=query_data.get('agent_id')
                ))
                warmed += 1
            except Exception as e:
                logger.error(f"❌ Failed to warm cache for query: {str(e)}")
        
        logger.info(f"🔥 Cache warmed with {warmed} queries")


# Global instance
redis_cache_layer = RedisCacheLayer()


# Usage example
"""
from services.cache.redis_cache_layer import redis_cache_layer

# Check cache before processing
cached_response = await redis_cache_layer.get_cached_response(
    query="What are your business hours?",
    company_id="company_123",
    agent_id="agent_456"
)

if cached_response:
    # Use cached response
    response = cached_response['response']
else:
    # Generate new response
    response = await generate_response(query)
    
    # Cache the response
    await redis_cache_layer.cache_response(
        query="What are your business hours?",
        response=response,
        company_id="company_123",
        agent_id="agent_456"
    )

# Get metrics
metrics = redis_cache_layer.get_metrics()
print(f"Hit rate: {metrics['hit_rate_percent']:.2f}%")

# Warm cache with common queries
common_queries = [
    {
        'query': 'What are your business hours?',
        'response': 'We are open Monday-Friday 9AM-5PM',
        'company_id': 'company_123',
        'agent_id': 'agent_456'
    }
]
redis_cache_layer.warm_cache(common_queries)
"""