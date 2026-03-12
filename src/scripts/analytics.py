# Native Imports
import asyncio
from abc import ABC, abstractmethod
from collections import Counter
from datetime import datetime, timezone, timedelta

# Local Imports
from src.utils.custom_logger import get_logger

# Create the global logger
logger = get_logger(name=__name__, file_name="analytics.log")


# Abstract Base Class
class AnalyticsService(ABC):
    """Abstract interface for analytics backends"""

    @abstractmethod
    async def record_order(
        self, order_response, status_code: int, orders: list
    ) -> None:
        """Record an order event for analytics tracking"""

    @abstractmethod
    async def get_summary(self) -> dict:
        """Return aggregate order counts and acceptance rate"""

    @abstractmethod
    async def get_top_skus(self, limit: int) -> list[dict]:
        """Return most-requested SKUs ranked by total quantity requested"""

    @abstractmethod
    async def get_hourly_trend(self, hours: int) -> list[dict]:
        """Return order counts bucketed by hour for the last N hours"""

    @abstractmethod
    async def get_event_log(self) -> list[dict]:
        """Return all recorded order events"""


# In-Memory Implementation
class InMemoryAnalytics(AnalyticsService):
    """In-memory analytics tracker, structured for easy DynamoDB swap"""

    def __init__(self, max_size: int = 10_000) -> None:
        self._events: list[dict] = []
        self._lock = asyncio.Lock()
        self.max_size = max_size

    async def record_order(
        self, order_response, status_code: int, orders: list
    ) -> None:
        status_map = {200: "accepted", 207: "partial", 409: "rejected"}
        status = status_map.get(status_code, "unknown")

        skus = []
        for order in orders:
            sku = order.sku
            quantity_requested = order.quantity
            # Check if this SKU was accepted
            accepted_item = next(
                (item for item in order_response.accepted_items if item.sku == sku),
                None,
            )
            if accepted_item:
                skus.append({
                    "sku": sku,
                    "quantity_requested": quantity_requested,
                    "quantity_accepted": accepted_item.accepted_quantity,
                    "accepted": True,
                })
            else:
                skus.append({
                    "sku": sku,
                    "quantity_requested": quantity_requested,
                    "quantity_accepted": 0,
                    "accepted": False,
                })

        accepted_count = len(order_response.accepted_items)
        rejected_count = len(order_response.rejected_items)

        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reservation_id": order_response.reservation_id,
            "status": status,
            "status_code": status_code,
            "total_items": accepted_count + rejected_count,
            "accepted_count": accepted_count,
            "rejected_count": rejected_count,
            "skus": skus,
        }

        async with self._lock:
            self._events.append(event)
            if len(self._events) > self.max_size:
                self._events.pop(0)

        logger.info(
            f"Recorded order {order_response.reservation_id}: "
            f"status={status}, {accepted_count} accepted, {rejected_count} rejected"
        )

    async def get_summary(self) -> dict:
        async with self._lock:
            events = list(self._events)

        total = len(events)
        accepted = sum(1 for e in events if e["status"] == "accepted")
        partial = sum(1 for e in events if e["status"] == "partial")
        rejected = sum(1 for e in events if e["status"] == "rejected")
        acceptance_rate = round(accepted / total, 4) if total > 0 else 0.0

        total_items_requested = sum(
            s["quantity_requested"] for e in events for s in e["skus"]
        )
        total_items_accepted = sum(
            s["quantity_accepted"] for e in events for s in e["skus"]
        )
        total_items_rejected = total_items_requested - total_items_accepted

        return {
            "total_orders": total,
            "accepted": accepted,
            "partial": partial,
            "rejected": rejected,
            "acceptance_rate": acceptance_rate,
            "total_items_requested": total_items_requested,
            "total_items_accepted": total_items_accepted,
            "total_items_rejected": total_items_rejected,
        }

    async def get_top_skus(self, limit: int = 5) -> list[dict]:
        async with self._lock:
            events = list(self._events)

        counter: Counter = Counter()
        for event in events:
            for s in event["skus"]:
                counter[s["sku"]] += s["quantity_requested"]

        return [
            {"sku": sku, "total_requested": count}
            for sku, count in counter.most_common(limit)
        ]

    async def get_hourly_trend(self, hours: int = 24) -> list[dict]:
        async with self._lock:
            events = list(self._events)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        buckets: Counter = Counter()

        for event in events:
            ts = datetime.fromisoformat(event["timestamp"])
            if ts >= cutoff:
                hour_key = ts.strftime("%Y-%m-%dT%H:00:00+00:00")
                buckets[hour_key] += 1

        return [
            {"hour": hour, "order_count": count}
            for hour, count in sorted(buckets.items())
        ]

    async def get_event_log(self) -> list[dict]:
        async with self._lock:
            return list(self._events)


# Module-level singleton
TRACKER = InMemoryAnalytics()
