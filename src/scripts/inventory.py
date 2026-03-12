# Native Imports
import asyncio
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta

# Local Imports
from src.scripts.inputs import Order
from src.scripts.responses import OrderItemResult, OrderResponse
from src.utils.custom_logger import get_logger

# Create the global logger
logger = get_logger(name=__name__, file_name="inventory.log")


# Abstract Base Class
class InventoryStore(ABC):
    """Abstract interface for inventory storage backends"""

    @abstractmethod
    async def check_stock(self, sku: str) -> int:
        """Read available quantity for a SKU"""

    @abstractmethod
    async def soft_reserve(
        self, sku: str, quantity: int, reservation_id: str, ttl_seconds: int
    ) -> int:
        """Temporarily hold stock. Returns the quantity actually reserved."""

    @abstractmethod
    async def confirm_reservation(self, reservation_id: str) -> bool:
        """Make a soft reservation permanent (for future payment integration)"""

    @abstractmethod
    async def release_reservation(self, reservation_id: str) -> bool:
        """Manually release a held reservation back to available stock"""

    @abstractmethod
    async def add_stock(self, sku: str, quantity: int) -> int:
        """Add quantity to a SKU (creates SKU if new). Returns new level."""

    @abstractmethod
    async def set_stock(self, sku: str, quantity: int) -> int:
        """Set exact stock level for a SKU (admin override). Returns new level."""

    @abstractmethod
    async def get_all_stock(self) -> dict[str, int]:
        """Return current stock levels"""


# In-Memory Implementation
class InMemoryInventory(InventoryStore):
    """In-memory inventory store for development, structured for easy DynamoDB swap"""

    def __init__(self, max_reservations: int = 50_000) -> None:
        self._stock: dict[str, int] = {
            "ABC123": 500,
            "DEF456": 300,
            "GHI789": 200,
            "12345": 100,
            "XYZ": 1000,
        }
        self._reservations: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self.max_reservations = max_reservations

    def _cleanup_expired(self) -> list[str]:
        """Release expired reservations back to available stock

        Returns:
            list[str]: Reservation IDs that were expired and released
        """
        now = datetime.now(timezone.utc)
        expired = [
            rid
            for rid, info in self._reservations.items()
            if info["expires_at"] <= now and not info["confirmed"]
        ]
        for rid in expired:
            info = self._reservations.pop(rid)
            self._stock[info["sku"]] += info["quantity"]
            logger.info(
                f"Released expired reservation {rid}: "
                f"{info['quantity']}x {info['sku']} back to stock"
            )
        return expired

    def _available_stock(self, sku: str) -> int:
        """Calculate available stock (total minus active reservations)"""
        return self._stock.get(sku, 0)

    async def run_cleanup(self) -> tuple[int, list[str]]:
        """Public interface: acquire lock, run cleanup, return count and expired IDs"""
        async with self._lock:
            expired_ids = self._cleanup_expired()
            return len(expired_ids), expired_ids

    async def check_stock(self, sku: str) -> int:
        async with self._lock:
            return self._available_stock(sku)

    async def soft_reserve(
        self, sku: str, quantity: int, reservation_id: str, ttl_seconds: int
    ) -> int:
        async with self._lock:
            if len(self._reservations) >= self.max_reservations:
                return 0
            available = self._available_stock(sku)
            reserved_qty = min(quantity, available)
            if reserved_qty > 0:
                self._stock[sku] -= reserved_qty
                self._reservations[reservation_id] = {
                    "sku": sku,
                    "quantity": reserved_qty,
                    "expires_at": datetime.now(timezone.utc)
                    + timedelta(seconds=ttl_seconds),
                    "confirmed": False,
                }
            return reserved_qty

    async def confirm_reservation(self, reservation_id: str) -> bool:
        async with self._lock:
            matching_keys = [
                rid for rid in self._reservations
                if rid == reservation_id or rid.startswith(f"{reservation_id}:")
            ]
            if not matching_keys:
                return False
            for rid in matching_keys:
                self._reservations[rid]["confirmed"] = True
                logger.info(f"Confirmed reservation {rid}")
            return True

    async def release_reservation(self, reservation_id: str) -> bool:
        async with self._lock:
            matching_keys = [
                rid for rid in self._reservations
                if rid == reservation_id or rid.startswith(f"{reservation_id}:")
            ]
            if not matching_keys:
                return False
            for rid in matching_keys:
                info = self._reservations.pop(rid)
                if not info["confirmed"]:
                    self._stock[info["sku"]] += info["quantity"]
                logger.info(f"Released reservation {rid}")
            return True

    async def add_stock(self, sku: str, quantity: int) -> int:
        async with self._lock:
            self._stock[sku] = self._stock.get(sku, 0) + quantity
            logger.info(f"Added {quantity} units to {sku}, new level: {self._stock[sku]}")
            return self._stock[sku]

    async def set_stock(self, sku: str, quantity: int) -> int:
        async with self._lock:
            self._stock[sku] = quantity
            logger.info(f"Set {sku} stock to {quantity}")
            return self._stock[sku]

    async def get_all_stock(self) -> dict[str, int]:
        async with self._lock:
            return dict(self._stock)


# Module-level singleton
INVENTORY = InMemoryInventory()


async def validate_and_reserve(
    orders: list[Order], store: InventoryStore, ttl_seconds: int = 3
) -> tuple[OrderResponse, int]:
    """Validate stock and create soft reservations for an order

    Args:
        orders: List of Order items to validate
        store: The inventory store to check against
        ttl_seconds: Time-to-live for the soft reservation (default 3s for testing)

    Returns:
        Tuple of (OrderResponse, HTTP status code)
    """

    accepted_items: list[OrderItemResult] = []
    rejected_items: list[OrderItemResult] = []
    reservation_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    for order in orders:
        reserved_qty = await store.soft_reserve(
            sku=order.sku,
            quantity=order.quantity,
            reservation_id=f"{reservation_id}:{order.sku}",
            ttl_seconds=ttl_seconds,
        )

        if reserved_qty == order.quantity:
            accepted_items.append(
                OrderItemResult(
                    sku=order.sku,
                    object=order.object,
                    requested_quantity=order.quantity,
                    accepted=True,
                    accepted_quantity=reserved_qty,
                )
            )
        elif reserved_qty > 0:
            accepted_items.append(
                OrderItemResult(
                    sku=order.sku,
                    object=order.object,
                    requested_quantity=order.quantity,
                    accepted=True,
                    accepted_quantity=reserved_qty,
                    reason=f"Only {reserved_qty} of {order.quantity} available",
                )
            )
        else:
            available = await store.check_stock(order.sku)
            reason = (
                "SKU not found in inventory"
                if available == 0
                and order.sku not in (await store.get_all_stock())
                else "Insufficient stock"
            )
            rejected_items.append(
                OrderItemResult(
                    sku=order.sku,
                    object=order.object,
                    requested_quantity=order.quantity,
                    accepted=False,
                    accepted_quantity=0,
                    reason=reason,
                )
            )

    # Determine status code and summary
    if rejected_items and not accepted_items:
        status_code = 409
        summary = "All items rejected — insufficient stock"
    elif rejected_items and accepted_items:
        status_code = 207
        summary = "Partial fulfillment — some items could not be reserved"
    else:
        status_code = 200
        summary = "All items reserved successfully"

    response = OrderResponse(
        summary=summary,
        reservation_id=reservation_id,
        expires_at=expires_at.isoformat(),
        accepted_items=accepted_items,
        rejected_items=rejected_items,
    )

    logger.info(
        f"Order {reservation_id}: {len(accepted_items)} accepted, "
        f"{len(rejected_items)} rejected (status {status_code})"
    )

    return response, status_code
