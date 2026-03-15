# Native Imports
import asyncio
from collections import defaultdict
from collections.abc import Callable

# Local Imports
from src.utils.custom_logger import get_logger

# Create the global logger
logger = get_logger(name=__name__, file_name="event_bus.log")


class EventBus:
    """Lightweight in-process async event bus for domain event dispatching"""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """Register a handler for a given event type name"""
        self._subscribers[event_type].append(handler)
        logger.info(f"Subscribed {handler.__name__} to {event_type}")

    async def publish_and_wait(self, event) -> None:
        """Publish an event and run all handlers sequentially, awaiting each one.

        Handler errors propagate to the caller.
        """
        event_type = type(event).__name__
        handlers = self._subscribers.get(event_type, [])
        for handler in handlers:
            await handler(event)

    async def publish(self, event) -> None:
        """Publish an event fire-and-forget via asyncio.create_task.

        Handler errors are logged but do not propagate.
        """
        event_type = type(event).__name__
        handlers = self._subscribers.get(event_type, [])
        for handler in handlers:
            asyncio.create_task(self._safe_call(handler, event))

    async def _safe_call(self, handler: Callable, event) -> None:
        """Call a handler, logging any exceptions instead of propagating"""
        try:
            await handler(event)
        except Exception:
            logger.exception(
                f"Handler {handler.__name__} failed for {type(event).__name__}"
            )


# Module-level singleton
EVENT_BUS = EventBus()
