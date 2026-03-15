# Native Imports
import asyncio
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone

# Local Imports
from src.utils.custom_logger import get_logger

# Create the global logger
logger = get_logger(name=__name__, file_name="notifications.log")


# Abstract Base Class
class NotificationService(ABC):
    """Abstract interface for notification backends"""

    @abstractmethod
    async def send_order_confirmation(
        self, recipient: str, channel: str, reservation_id: str, items: list[dict]
    ) -> str:
        """Send an order confirmation notification. Returns notification_id."""

    @abstractmethod
    async def send_shipping_confirmation(
        self, recipient: str, channel: str, reservation_id: str, tracking_number: str
    ) -> str:
        """Send a shipping confirmation notification. Returns notification_id."""

    @abstractmethod
    async def get_notification_log(self) -> list[dict]:
        """Return all sent notification records"""


# Mock Implementation
class MockNotificationService(NotificationService):
    """In-memory mock notification service, structured for easy SNS swap"""

    def __init__(self, max_size: int = 10_000) -> None:
        self._notifications: list[dict] = []
        self._lock = asyncio.Lock()
        self.max_size = max_size

    async def send_order_confirmation(
        self, recipient: str, channel: str, reservation_id: str, items: list[dict]
    ) -> str:
        notification_id = str(uuid.uuid4())
        record = {
            "notification_id": notification_id,
            "recipient": recipient,
            "channel": channel,
            "event_type": "order_confirmation",
            "payload": {
                "reservation_id": reservation_id,
                "items": items,
            },
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        async with self._lock:
            self._notifications.append(record)
            if len(self._notifications) > self.max_size:
                self._notifications.pop(0)
        logger.info(
            f"Sent order confirmation {notification_id} to {recipient} "
            f"via {channel} for reservation {reservation_id}"
        )
        return notification_id

    async def send_shipping_confirmation(
        self, recipient: str, channel: str, reservation_id: str, tracking_number: str
    ) -> str:
        notification_id = str(uuid.uuid4())
        record = {
            "notification_id": notification_id,
            "recipient": recipient,
            "channel": channel,
            "event_type": "shipping_confirmation",
            "payload": {
                "reservation_id": reservation_id,
                "tracking_number": tracking_number,
            },
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        async with self._lock:
            self._notifications.append(record)
            if len(self._notifications) > self.max_size:
                self._notifications.pop(0)
        logger.info(
            f"Sent shipping confirmation {notification_id} to {recipient} "
            f"via {channel} for reservation {reservation_id}, "
            f"tracking: {tracking_number}"
        )
        return notification_id

    async def get_notification_log(self) -> list[dict]:
        async with self._lock:
            return list(self._notifications)


# Module-level singleton
NOTIFIER = MockNotificationService()
