"""
Thin async Redis wrapper. Keeps all cache key logic in one place.
"""
from __future__ import annotations

from typing import Optional
import redis.asyncio as redis


class CacheClient:
    def __init__(self, redis_url: str) -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)

    async def get(self, key: str) -> Optional[str]:
        return await self._redis.get(key)

    async def set(self, key: str, value: str, ex: int = 3600) -> None:
        await self._redis.set(key, value, ex=ex)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def close(self) -> None:
        await self._redis.aclose()
