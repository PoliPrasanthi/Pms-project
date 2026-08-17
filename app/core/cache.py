import json
from typing import Optional, Any
import functools
from fastapi.encoders import jsonable_encoder
import redis.asyncio as redis
from app.core.config import settings

import time

class RedisCache:
    def __init__(self):
        self.redis: Optional[redis.Redis] = None
        self._memory_cache = {}

    async def connect(self):
        if settings.REDIS_URL:
            try:
                self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
            except Exception:
                self.redis = None

    async def close(self):
        if self.redis:
            await self.redis.close()

    async def get(self, key: str) -> Optional[Any]:
        if self.redis:
            try:
                val = await self.redis.get(key)
                return json.loads(val) if val else None
            except Exception:
                pass
        
        # Fallback to in-memory
        if key in self._memory_cache:
            entry = self._memory_cache[key]
            if entry["expire_at"] > time.time():
                return entry["value"]
            else:
                del self._memory_cache[key]
        return None

    async def set(self, key: str, value: Any, expire: int = 3600):
        if self.redis:
            try:
                await self.redis.set(key, json.dumps(value), ex=expire)
                return
            except Exception:
                pass
        
        # Fallback to in-memory
        self._memory_cache[key] = {
            "value": value,
            "expire_at": time.time() + expire
        }

    async def delete(self, key: str):
        if self.redis:
            try:
                await self.redis.delete(key)
                return
            except Exception:
                pass
                
        if key in self._memory_cache:
            del self._memory_cache[key]

    async def delete_pattern(self, pattern: str):
        if self.redis:
            try:
                keys = await self.redis.keys(pattern)
                if keys:
                    await self.redis.delete(*keys)
                return
            except Exception:
                pass
        
        # Fallback to in-memory (simple prefix match since pattern usually ends with *)
        prefix = pattern.replace('*', '')
        keys_to_delete = [k for k in self._memory_cache.keys() if k.startswith(prefix)]
        for k in keys_to_delete:
            del self._memory_cache[k]

cache = RedisCache()

def cached(ttl: int = 10800, key_prefix: str = ""):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            key_parts = [key_prefix or func.__name__]
            for arg in args:
                if isinstance(arg, (str, int, float, bool)):
                    key_parts.append(str(arg))
            for k, v in sorted(kwargs.items()):
                if isinstance(v, (str, int, float, bool)):
                    key_parts.append(f"{k}:{v}")
            
            cache_key = ":".join(key_parts)
            
            cached_value = await cache.get(cache_key)
            if cached_value is not None:
                return cached_value
                
            result = await func(*args, **kwargs)
            
            await cache.set(cache_key, jsonable_encoder(result), expire=ttl)
            return result
        return wrapper
    return decorator

async def invalidate_cache(key_prefix: str):
    """Helper to explicitly invalidate cache entries by prefix (Write-Through/Eviction)"""
    await cache.delete_pattern(f"{key_prefix}*")

def endpoint_cache(ttl: int = 60, prefix: str = "api"):
    """
    Cache decorator tailored for FastAPI endpoints.
    Automatically isolates cache keys by user_id to guarantee security.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            key_parts = [prefix]
            
            # Secure User Isolation
            user_id = "anon"
            if "current_user" in kwargs and hasattr(kwargs["current_user"], "id"):
                user_id = str(kwargs["current_user"].id)
                
            key_parts.append(f"usr_{user_id}")
            
            # Include query parameters in the key
            for k, v in sorted(kwargs.items()):
                if k in ["db", "current_user", "request", "response"]:
                    continue
                if v is None:
                    continue
                if isinstance(v, (str, int, float, bool)):
                    key_parts.append(f"{k}_{v}")
                elif isinstance(v, list):
                    key_parts.append(f"{k}_{','.join(str(i) for i in sorted(v))}")
                    
            cache_key = ":".join(key_parts)
            
            cached_value = await cache.get(cache_key)
            if cached_value is not None:
                return cached_value
                
            result = await func(*args, **kwargs)
            
            await cache.set(cache_key, jsonable_encoder(result), expire=ttl)
            return result
        return wrapper
    return decorator
