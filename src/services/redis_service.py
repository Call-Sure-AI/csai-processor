"""
Simple Redis Service for key-value datastore and caching
"""
import valkey
import json
import logging
from typing import Any, Optional
from config.settings import settings

logger = logging.getLogger(__name__)


class RedisService:
    """Simple Redis service for key-value storage and caching"""
    
    def __init__(self, redis_url: Optional[str] = None):
        """Initialize Redis service"""
        self.redis_url = redis_url or settings.redis_url
        self.client = None
        
    def connect(self) -> bool:
        """Connect to Redis server"""
        try:
            if self.client is None:
                self.client = valkey.from_url(self.redis_url)
                self.client.ping()  # Test connection
                logger.info("Connected to Redis")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {str(e)}")
            return False
    
    def disconnect(self):
        """Disconnect from Redis server"""
        try:
            if self.client:
                self.client.close()
                self.client = None
                logger.info("Disconnected from Redis")
        except Exception as e:
            logger.error(f"Error disconnecting from Redis: {str(e)}")
    
    def _ensure_connected(self):
        """Ensure we're connected to Redis"""
        if not self.client:
            self.connect()
    
    # Basic Operations
    def set(self, key: str, value: Any, expire: Optional[int] = None) -> bool:
        """Set a key-value pair"""
        try:
            self._ensure_connected()
            
            # Convert to JSON if not string
            if not isinstance(value, str):
                value = json.dumps(value)
            
            if expire:
                return self.client.setex(key, expire, value)
            else:
                return self.client.set(key, value)
        except Exception as e:
            logger.error(f"Error setting key {key}: {str(e)}")
            return False
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get value by key"""
        try:
            self._ensure_connected()
            
            value = self.client.get(key)
            if value is None:
                return default
            
            # Try to parse as JSON, fallback to string
            try:
                return json.loads(value.decode('utf-8'))
            except:
                return value.decode('utf-8')
        except Exception as e:
            logger.error(f"Error getting key {key}: {str(e)}")
            return default
    
    def delete(self, key: str) -> bool:
        """Delete a key"""
        try:
            self._ensure_connected()
            return bool(self.client.delete(key))
        except Exception as e:
            logger.error(f"Error deleting key {key}: {str(e)}")
            return False
    
    def exists(self, key: str) -> bool:
        """Check if key exists"""
        try:
            self._ensure_connected()
            return bool(self.client.exists(key))
        except Exception as e:
            logger.error(f"Error checking key {key}: {str(e)}")
            return False
    
    def expire(self, key: str, seconds: int) -> bool:
        """Set expiration time for a key"""
        try:
            self._ensure_connected()
            return bool(self.client.expire(key, seconds))
        except Exception as e:
            logger.error(f"Error setting expiration for {key}: {str(e)}")
            return False
    
    # Hash Operations
    def hset(self, key: str, field: str, value: Any) -> bool:
        """Set field in hash"""
        try:
            self._ensure_connected()
            
            if not isinstance(value, str):
                value = json.dumps(value)
            
            return bool(self.client.hset(key, field, value))
        except Exception as e:
            logger.error(f"Error setting hash field {key}:{field}: {str(e)}")
            return False
    
    def hget(self, key: str, field: str, default: Any = None) -> Any:
        """Get field from hash"""
        try:
            self._ensure_connected()
            
            value = self.client.hget(key, field)
            if value is None:
                return default
            
            try:
                return json.loads(value.decode('utf-8'))
            except:
                return value.decode('utf-8')
        except Exception as e:
            logger.error(f"Error getting hash field {key}:{field}: {str(e)}")
            return default
    
    def hgetall(self, key: str) -> dict:
        """Get all fields from hash"""
        try:
            self._ensure_connected()
            
            hash_data = self.client.hgetall(key)
            result = {}
            
            for field, value in hash_data.items():
                field_str = field.decode('utf-8')
                try:
                    result[field_str] = json.loads(value.decode('utf-8'))
                except:
                    result[field_str] = value.decode('utf-8')
            
            return result
        except Exception as e:
            logger.error(f"Error getting hash {key}: {str(e)}")
            return {}
    
    def hdel(self, key: str, field: str) -> bool:
        """Delete field from hash"""
        try:
            self._ensure_connected()
            return bool(self.client.hdel(key, field))
        except Exception as e:
            logger.error(f"Error deleting hash field {key}:{field}: {str(e)}")
            return False
    
    # List Operations
    def lpush(self, key: str, value: Any) -> bool:
        """Push value to left of list"""
        try:
            self._ensure_connected()
            
            if not isinstance(value, str):
                value = json.dumps(value)
            
            return bool(self.client.lpush(key, value))
        except Exception as e:
            logger.error(f"Error pushing to list {key}: {str(e)}")
            return False
    
    def rpush(self, key: str, value: Any) -> bool:
        """Push value to right of list"""
        try:
            self._ensure_connected()
            
            if not isinstance(value, str):
                value = json.dumps(value)
            
            return bool(self.client.rpush(key, value))
        except Exception as e:
            logger.error(f"Error pushing to list {key}: {str(e)}")
            return False
    
    def lpop(self, key: str, default: Any = None) -> Any:
        """Pop value from left of list"""
        try:
            self._ensure_connected()
            
            value = self.client.lpop(key)
            if value is None:
                return default
            
            try:
                return json.loads(value.decode('utf-8'))
            except:
                return value.decode('utf-8')
        except Exception as e:
            logger.error(f"Error popping from list {key}: {str(e)}")
            return default
    
    def lrange(self, key: str, start: int = 0, end: int = -1) -> list:
        """Get range of values from list"""
        try:
            self._ensure_connected()
            
            values = self.client.lrange(key, start, end)
            result = []
            
            for value in values:
                try:
                    result.append(json.loads(value.decode('utf-8')))
                except:
                    result.append(value.decode('utf-8'))
            
            return result
        except Exception as e:
            logger.error(f"Error getting list range {key}: {str(e)}")
            return []
    
    # Cache Operations (convenience methods)
    def cache_set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set value with TTL (cache operation)"""
        return self.set(key, value, expire=ttl)
    
    def cache_get(self, key: str, default: Any = None) -> Any:
        """Get cached value"""
        return self.get(key, default=default)
    
    def cache_delete(self, key: str) -> bool:
        """Delete cached value"""
        return self.delete(key)
    
    def cache_exists(self, key: str) -> bool:
        """Check if cached value exists"""
        return self.exists(key)
    
    # Utility Methods
    def keys(self, pattern: str = "*") -> list:
        """Get keys matching pattern"""
        try:
            self._ensure_connected()
            keys = self.client.keys(pattern)
            return [key.decode('utf-8') for key in keys]
        except Exception as e:
            logger.error(f"Error getting keys with pattern {pattern}: {str(e)}")
            return []
    
    def flush_db(self) -> bool:
        """Flush current database"""
        try:
            self._ensure_connected()
            return bool(self.client.flushdb())
        except Exception as e:
            logger.error(f"Error flushing database: {str(e)}")
            return False


# Global Redis service instance
_redis_service = None


def get_redis_service() -> RedisService:
    """Get global Redis service instance"""
    global _redis_service
    if _redis_service is None:
        _redis_service = RedisService()
        _redis_service.connect()
    return _redis_service


def init_redis_service(redis_url: Optional[str] = None) -> RedisService:
    """Initialize global Redis service"""
    global _redis_service
    _redis_service = RedisService(redis_url)
    _redis_service.connect()
    return _redis_service
