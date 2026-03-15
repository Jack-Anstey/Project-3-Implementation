# Native Imports
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta

# Local Imports
from src.utils.custom_logger import get_logger

# Create the global logger
logger = get_logger(name=__name__, file_name="idempotency.log")


# Abstract Base Class
class IdempotencyStore(ABC):
    """Abstract interface for idempotency backends"""

    @abstractmethod
    async def get(self, key: str) -> dict | None:
        """Returns {"response": ..., "status_code": ...} or None"""

    @abstractmethod
    async def put(
        self, key: str, response: dict, status_code: int, ttl_seconds: int
    ) -> None:
        """Store a response with TTL"""


# In-Memory Implementation
class InMemoryIdempotencyStore(IdempotencyStore):
    """In-memory idempotency store, structured for easy DynamoDB swap"""

    def __init__(self, max_size: int = 5000) -> None:
        self._cache: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self.max_size = max_size

    async def get(self, key: str) -> dict | None:
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if datetime.now(timezone.utc) >= entry["expires_at"]:
                del self._cache[key]
                return None
            return {"response": entry["response"], "status_code": entry["status_code"]}

    async def put(
        self, key: str, response: dict, status_code: int, ttl_seconds: int
    ) -> None:
        async with self._lock:
            self._cache[key] = {
                "response": response,
                "status_code": status_code,
                "expires_at": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
            }
            if len(self._cache) > self.max_size:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
                logger.info(f"Idempotency cache cap reached, evicted oldest key")
        logger.info(f"Cached idempotency key '{key}' with TTL={ttl_seconds}s")


# Module-level singleton
IDEMPOTENCY_STORE = InMemoryIdempotencyStore()
