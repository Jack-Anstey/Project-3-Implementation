# Native Imports
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum

# Local Imports
from src.utils.custom_logger import get_logger

# Create the global logger
logger = get_logger(name=__name__, file_name="saga.log")


# Saga Status Enum
class OrderSagaStatus(str, Enum):
    """Valid states in the order saga lifecycle"""

    RECEIVED = "RECEIVED"
    RESERVED = "RESERVED"
    PARTIALLY_RESERVED = "PARTIALLY_RESERVED"
    REJECTED = "REJECTED"
    NOTIFYING = "NOTIFYING"
    CONFIRMED = "CONFIRMED"
    COMPENSATING = "COMPENSATING"
    COMPENSATION_COMPLETE = "COMPENSATION_COMPLETE"
    EXPIRED = "EXPIRED"


# Legal transitions: from_status -> set of allowed to_statuses
_LEGAL_TRANSITIONS: dict[OrderSagaStatus, set[OrderSagaStatus]] = {
    OrderSagaStatus.RECEIVED: {
        OrderSagaStatus.RESERVED,
        OrderSagaStatus.PARTIALLY_RESERVED,
        OrderSagaStatus.REJECTED,
    },
    OrderSagaStatus.RESERVED: {
        OrderSagaStatus.NOTIFYING,
        OrderSagaStatus.CONFIRMED,
        OrderSagaStatus.COMPENSATING,
        OrderSagaStatus.EXPIRED,
    },
    OrderSagaStatus.PARTIALLY_RESERVED: {
        OrderSagaStatus.NOTIFYING,
        OrderSagaStatus.CONFIRMED,
        OrderSagaStatus.COMPENSATING,
        OrderSagaStatus.EXPIRED,
    },
    OrderSagaStatus.NOTIFYING: {
        OrderSagaStatus.CONFIRMED,
        OrderSagaStatus.COMPENSATING,
        OrderSagaStatus.EXPIRED,
    },
    OrderSagaStatus.COMPENSATING: {
        OrderSagaStatus.COMPENSATION_COMPLETE,
    },
    # Terminal states — no transitions out
    OrderSagaStatus.REJECTED: set(),
    OrderSagaStatus.CONFIRMED: set(),
    OrderSagaStatus.COMPENSATION_COMPLETE: set(),
    OrderSagaStatus.EXPIRED: set(),
}


# Abstract Base Class
class SagaTracker(ABC):
    """Abstract interface for saga state tracking backends"""

    @abstractmethod
    async def start_saga(
        self,
        reservation_id: str,
        orders: list,
        idempotency_key: str | None = None,
    ) -> dict:
        """Create a new saga in RECEIVED state. Returns the saga record."""

    @abstractmethod
    async def transition(
        self,
        reservation_id: str,
        new_status: OrderSagaStatus,
        metadata: dict | None = None,
    ) -> bool:
        """Transition a saga to a new status. Returns True if transition occurred."""

    @abstractmethod
    async def get_saga(self, reservation_id: str) -> dict | None:
        """Retrieve a saga by reservation_id, or None if not found."""

    @abstractmethod
    async def get_all_sagas(self) -> list[dict]:
        """Return all tracked sagas."""


# In-Memory Implementation
class InMemorySagaTracker(SagaTracker):
    """In-memory saga tracker, structured for easy Step Functions swap"""

    def __init__(self, max_sagas: int = 10_000) -> None:
        self._sagas: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self.max_sagas = max_sagas

    async def start_saga(
        self,
        reservation_id: str,
        orders: list,
        idempotency_key: str | None = None,
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        saga = {
            "reservation_id": reservation_id,
            "status": OrderSagaStatus.RECEIVED.value,
            "orders": [
                {"sku": getattr(o, "sku", ""), "quantity": getattr(o, "quantity", 0)}
                for o in orders
            ],
            "idempotency_key": idempotency_key,
            "created_at": now,
            "updated_at": now,
            "history": [
                {
                    "status": OrderSagaStatus.RECEIVED.value,
                    "timestamp": now,
                    "metadata": None,
                }
            ],
        }
        async with self._lock:
            # FIFO eviction if at capacity
            if len(self._sagas) >= self.max_sagas:
                oldest_key = next(iter(self._sagas))
                self._sagas.pop(oldest_key)
                logger.warning(
                    f"Saga cap reached, evicted oldest saga {oldest_key}"
                )
            self._sagas[reservation_id] = saga
        logger.info(f"Saga started: {reservation_id} ->RECEIVED")
        return saga

    async def transition(
        self,
        reservation_id: str,
        new_status: OrderSagaStatus,
        metadata: dict | None = None,
    ) -> bool:
        async with self._lock:
            saga = self._sagas.get(reservation_id)
            if saga is None:
                logger.warning(
                    f"Saga transition skipped: {reservation_id} not found "
                    f"(target: {new_status.value})"
                )
                return False

            current = OrderSagaStatus(saga["status"])
            allowed = _LEGAL_TRANSITIONS.get(current, set())

            if new_status not in allowed:
                logger.warning(
                    f"Saga transition skipped: {reservation_id} "
                    f"{current.value} ->{new_status.value} is not a legal transition"
                )
                return False

            now = datetime.now(timezone.utc).isoformat()
            saga["status"] = new_status.value
            saga["updated_at"] = now
            saga["history"].append(
                {
                    "status": new_status.value,
                    "timestamp": now,
                    "metadata": metadata,
                }
            )

        logger.info(
            f"Saga transition: {reservation_id} "
            f"{current.value} ->{new_status.value}"
        )
        return True

    async def get_saga(self, reservation_id: str) -> dict | None:
        async with self._lock:
            saga = self._sagas.get(reservation_id)
            return dict(saga) if saga else None

    async def get_all_sagas(self) -> list[dict]:
        async with self._lock:
            return list(self._sagas.values())


# Module-level singleton
SAGA_TRACKER = InMemorySagaTracker()
