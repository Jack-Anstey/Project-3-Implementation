# Native Imports
import asyncio
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime, timezone, timedelta

# Local Imports
from src.scripts.responses import RetryTaskResponse
from src.utils.custom_logger import get_logger

# Create the global logger
logger = get_logger(name=__name__, file_name="retry.log")


# Abstract Base Class
class RetryQueue(ABC):
    """Abstract interface for retry queue backends"""

    @abstractmethod
    async def enqueue(
        self, callable_name: str, *args, max_retries: int = 3, **kwargs
    ) -> str:
        """Add a task to the retry queue. Returns task_id."""

    @abstractmethod
    async def get_pending(self) -> list[RetryTaskResponse]:
        """Return all pending retry tasks"""

    @abstractmethod
    async def get_dead_letters(self) -> list[RetryTaskResponse]:
        """Return all permanently failed tasks"""

    @abstractmethod
    async def process_pending(self) -> int:
        """Process ready tasks. Returns number of tasks processed."""


# In-Memory Implementation
class InMemoryRetryQueue(RetryQueue):
    """In-memory retry queue, structured for easy SQS swap"""

    def __init__(
        self, max_queue_size: int = 1000, max_dead_letters: int = 500
    ) -> None:
        self._pending: list[dict] = []
        self._dead_letters: list[dict] = []
        self._callable_registry: dict[str, Callable] = {}
        self._lock = asyncio.Lock()
        self.max_queue_size = max_queue_size
        self.max_dead_letters = max_dead_letters

    def register_handler(self, name: str, handler: Callable) -> None:
        """Register a callable handler by name"""
        self._callable_registry[name] = handler
        logger.info(f"Registered retry handler: {name}")

    async def enqueue(
        self, callable_name: str, *args, max_retries: int = 3, **kwargs
    ) -> str:
        task_id = str(uuid.uuid4())
        task = {
            "task_id": task_id,
            "callable_name": callable_name,
            "args": args,
            "kwargs": kwargs,
            "attempts": 0,
            "max_retries": max_retries,
            "next_retry_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_error": None,
        }
        async with self._lock:
            self._pending.append(task)
            if len(self._pending) > self.max_queue_size:
                evicted = self._pending.pop(0)
                logger.warning(f"Queue cap reached, evicted oldest task {evicted['task_id']}")
        logger.info(f"Enqueued task {task_id} for handler '{callable_name}'")
        return task_id

    async def get_pending(self) -> list[RetryTaskResponse]:
        async with self._lock:
            return [
                RetryTaskResponse(
                    task_id=task["task_id"],
                    callable_name=task["callable_name"],
                    attempts=task["attempts"],
                    max_retries=task["max_retries"],
                    next_retry_at=task["next_retry_at"],
                    created_at=task["created_at"],
                    last_error=task["last_error"],
                )
                for task in self._pending
            ]

    async def get_dead_letters(self) -> list[RetryTaskResponse]:
        async with self._lock:
            return [
                RetryTaskResponse(
                    task_id=task["task_id"],
                    callable_name=task["callable_name"],
                    attempts=task["attempts"],
                    max_retries=task["max_retries"],
                    next_retry_at=task.get("next_retry_at"),
                    created_at=task["created_at"],
                    last_error=task["last_error"],
                )
                for task in self._dead_letters
            ]

    async def process_pending(self) -> int:
        now = datetime.now(timezone.utc)
        processed = 0

        async with self._lock:
            remaining = []
            for task in self._pending:
                next_retry = datetime.fromisoformat(task["next_retry_at"])
                if next_retry > now:
                    remaining.append(task)
                    continue

                handler = self._callable_registry.get(task["callable_name"])
                if not handler:
                    task["last_error"] = f"No handler registered for '{task['callable_name']}'"
                    self._dead_letters.append(task)
                    if len(self._dead_letters) > self.max_dead_letters:
                        self._dead_letters.pop(0)
                    logger.error(f"Task {task['task_id']}: no handler for '{task['callable_name']}'")
                    processed += 1
                    continue

                task["attempts"] += 1
                try:
                    await handler(*task["args"], **task["kwargs"])
                    logger.info(f"Task {task['task_id']} succeeded on attempt {task['attempts']}")
                    processed += 1
                except Exception as exc:
                    task["last_error"] = str(exc)
                    if task["attempts"] >= task["max_retries"]:
                        self._dead_letters.append(task)
                        if len(self._dead_letters) > self.max_dead_letters:
                            self._dead_letters.pop(0)
                        logger.warning(
                            f"Task {task['task_id']} dead-lettered after {task['attempts']} attempts: {exc}"
                        )
                    else:
                        backoff = 2 ** task["attempts"]
                        task["next_retry_at"] = (
                            now + timedelta(seconds=backoff)
                        ).isoformat()
                        remaining.append(task)
                        logger.info(
                            f"Task {task['task_id']} failed attempt {task['attempts']}, "
                            f"next retry in {backoff}s: {exc}"
                        )
                    processed += 1

            self._pending = remaining
        return processed


# Module-level singleton
RETRY_QUEUE = InMemoryRetryQueue()
