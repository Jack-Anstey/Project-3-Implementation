# Local Imports
from src.scripts.saga import SAGA_TRACKER, OrderSagaStatus
from src.scripts.retry_queue import RETRY_QUEUE
from src.scripts.idempotency import IDEMPOTENCY_STORE
from src.utils.custom_logger import get_logger

# Create the global logger
logger = get_logger(name=__name__, file_name="event_handlers.log")


# --- OrderValidated handlers ---


async def saga_on_order_validated(event) -> None:
    """Start saga, transition to reservation status, cache idempotency, and transition to NOTIFYING"""
    saga_status_map = {
        200: OrderSagaStatus.RESERVED,
        207: OrderSagaStatus.PARTIALLY_RESERVED,
        409: OrderSagaStatus.REJECTED,
    }

    await SAGA_TRACKER.start_saga(
        event.reservation_id, event.orders, event.idempotency_key
    )
    await SAGA_TRACKER.transition(
        event.reservation_id, saga_status_map[event.status_code]
    )

    # Cache response if idempotency key provided
    if event.idempotency_key:
        await IDEMPOTENCY_STORE.put(
            event.idempotency_key,
            event.order_response.model_dump(),
            event.status_code,
            ttl_seconds=300,
        )

    # Transition to NOTIFYING on success or partial success
    if event.status_code in (200, 207):
        await SAGA_TRACKER.transition(
            event.reservation_id, OrderSagaStatus.NOTIFYING
        )


async def notify_on_order_validated(event) -> None:
    """Enqueue order confirmation notification for successful/partial orders"""
    if event.status_code not in (200, 207):
        return

    await RETRY_QUEUE.enqueue(
        "send_order_confirmation",
        "customer@placeholder.local",
        "email",
        event.order_response.reservation_id,
        [
            {
                "sku": item.sku,
                "object": item.object,
                "quantity": item.accepted_quantity,
            }
            for item in event.order_response.accepted_items
        ],
    )


async def analytics_on_order_validated(event) -> None:
    """Enqueue analytics recording for ALL orders"""
    await RETRY_QUEUE.enqueue(
        "record_order",
        event.order_response,
        event.status_code,
        event.orders,
    )


# --- ReservationConfirmed handlers ---


async def saga_on_confirmed(event) -> None:
    """Transition saga to CONFIRMED"""
    await SAGA_TRACKER.transition(event.reservation_id, OrderSagaStatus.CONFIRMED)


# --- ReservationReleased handlers ---


async def saga_on_released(event) -> None:
    """Transition saga through COMPENSATING to COMPENSATION_COMPLETE"""
    await SAGA_TRACKER.transition(event.reservation_id, OrderSagaStatus.COMPENSATING)
    await SAGA_TRACKER.transition(
        event.reservation_id, OrderSagaStatus.COMPENSATION_COMPLETE
    )


# --- ReservationExpired handlers ---


async def saga_on_expired(event) -> None:
    """Transition saga to EXPIRED"""
    await SAGA_TRACKER.transition(event.reservation_id, OrderSagaStatus.EXPIRED)


# --- Registration ---


def register_all(bus) -> None:
    """Wire all event handlers to the given EventBus"""
    bus.subscribe("OrderValidated", saga_on_order_validated)
    bus.subscribe("OrderValidated", notify_on_order_validated)
    bus.subscribe("OrderValidated", analytics_on_order_validated)
    bus.subscribe("ReservationConfirmed", saga_on_confirmed)
    bus.subscribe("ReservationReleased", saga_on_released)
    bus.subscribe("ReservationExpired", saga_on_expired)
    logger.info("All event handlers registered")
