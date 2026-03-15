# Third Party Imports
from fastapi import APIRouter, Header, Query, Response
from fastapi.requests import Request

# Local Imports
from src.scripts.responses import *
from src.scripts.inputs import *
from src.scripts.inventory import INVENTORY, validate_and_reserve
from src.scripts.notifications import NOTIFIER
from src.scripts.analytics import TRACKER
from src.scripts.retry_queue import RETRY_QUEUE
from src.scripts.idempotency import IDEMPOTENCY_STORE
from src.scripts.saga import SAGA_TRACKER
from src.scripts.event_bus import EVENT_BUS
from src.scripts.events import OrderValidated, ReservationConfirmed, ReservationReleased
from src.utils.custom_logger import get_logger

# Create the global logger
logger = get_logger(name=__name__, file_name="orders.log")


# Define the router that we will use
ROUTER = APIRouter()


# Define get and post requests
@ROUTER.get(
    "/health",
    tags=["Health"],
    summary="Application health check",
    description="Returns a simple healthy response to confirm the API is running.",
    response_description="Health status message",
)
async def root_status(request: Request) -> BasicResponse:
    """Returns a simple healthy response to confirm the API is running.

    Use this endpoint for liveness probes or uptime monitors.
    """

    logger.info("Logging works for the root endpoint")
    return BasicResponse(response="Healthy!")


@ROUTER.post(
    "/order-intake",
    tags=["Orders"],
    summary="Submit orders and reserve inventory",
    description="Validates each item against live inventory, creates soft reservations for "
    "available stock, and returns accepted/rejected results. Reservations expire after "
    "3 seconds if not confirmed via `/inventory/confirm`.",
    response_description="Order validation results with accepted/rejected items and a reservation ID",
    responses={
        207: {"description": "Partial fulfillment — some items reserved, others rejected"},
        409: {"description": "All items rejected — insufficient stock or invalid SKUs"},
    },
)
async def take_order(
    request: Request,
    orders: list[Order],
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        description="Optional key for duplicate request detection",
    ),
) -> OrderResponse:
    """Validates each item against live inventory and creates soft reservations.

    **Tip:** Call `GET /inventory` first to check stock levels before submitting an order.

    - **200** — All items successfully reserved.
    - **207** — Mixed result: some items reserved, others rejected (invalid SKU or insufficient stock).
    - **409** — All items rejected.

    Pass an `Idempotency-Key` header to safely retry without creating duplicate reservations.
    Reservations expire after 3 seconds — call `POST /inventory/confirm` promptly.
    """

    # Idempotency check
    if idempotency_key is not None:
        cached = await IDEMPOTENCY_STORE.get(idempotency_key)
        if cached:
            response.status_code = cached["status_code"]
            return OrderResponse(**cached["response"])

    # Log the user's order
    logger.info(f"The user's order:")
    [logger.info(f"\t{order}") for order in orders]

    # Validate stock and create soft reservations
    order_response, status_code = await validate_and_reserve(orders, INVENTORY)
    response.status_code = status_code

    # Publish domain event — side-effects handled by subscribers
    await EVENT_BUS.publish_and_wait(OrderValidated(
        reservation_id=order_response.reservation_id,
        orders=orders,
        order_response=order_response,
        status_code=status_code,
        idempotency_key=idempotency_key,
    ))

    return order_response


@ROUTER.get(
    "/inventory",
    tags=["Diagnostics"],
    summary="Get current stock levels for all SKUs",
    description="Returns a map of every known SKU to its current available quantity. "
    "Useful for checking stock before submitting orders.",
    response_description="Map of SKU to available quantity",
)
async def get_inventory(request: Request) -> dict[str, int]:
    """Returns a map of every known SKU to its current available quantity.

    Use this before calling `POST /order-intake` to verify stock levels.
    """

    return await INVENTORY.get_all_stock()


@ROUTER.post(
    "/inventory/confirm",
    tags=["Inventory"],
    summary="Confirm a soft reservation",
    description="Permanently commits a soft reservation created by `POST /order-intake`. "
    "Must be called within 3 seconds of the reservation or it will have expired.",
    response_description="Confirmation result with reservation status",
    responses={
        404: {"description": "Reservation not found or already expired"},
    },
)
async def confirm_reservation(
    request: Request, body: ReservationAction, response: Response
) -> ReservationResponse:
    """Permanently commits a soft reservation created by `POST /order-intake`.

    Must be called within 3 seconds or the reservation expires automatically.

    - **200** — Reservation confirmed; stock is permanently deducted.
    - **404** — Reservation not found (invalid ID or already expired/released).
    """

    found = await INVENTORY.confirm_reservation(body.reservation_id)
    if not found:
        response.status_code = 404
        return ReservationResponse(
            reservation_id=body.reservation_id, status="not_found"
        )
    await EVENT_BUS.publish_and_wait(ReservationConfirmed(reservation_id=body.reservation_id))
    return ReservationResponse(
        reservation_id=body.reservation_id, status="confirmed"
    )


@ROUTER.post(
    "/inventory/release",
    tags=["Inventory"],
    summary="Release a soft reservation",
    description="Cancels a soft reservation and returns the held stock to available inventory. "
    "Use this when payment fails or the customer cancels. "
    "Must be called within 3 seconds or the reservation expires automatically.",
    response_description="Release result with reservation status",
    responses={
        404: {"description": "Reservation not found or already expired"},
    },
)
async def release_reservation(
    request: Request, body: ReservationAction, response: Response
) -> ReservationResponse:
    """Cancels a soft reservation and returns held stock to available inventory.

    Use this when payment fails or the customer cancels. Must be called within
    3 seconds or the reservation expires automatically.

    - **200** — Reservation released; stock is returned to available inventory.
    - **404** — Reservation not found (invalid ID or already expired/confirmed).
    """

    found = await INVENTORY.release_reservation(body.reservation_id)
    if not found:
        response.status_code = 404
        return ReservationResponse(
            reservation_id=body.reservation_id, status="not_found"
        )
    await EVENT_BUS.publish_and_wait(ReservationReleased(reservation_id=body.reservation_id))
    return ReservationResponse(
        reservation_id=body.reservation_id, status="released"
    )


@ROUTER.post(
    "/inventory/restock",
    tags=["Diagnostics"],
    summary="Add stock to a SKU",
    description="Increases the available quantity for an existing SKU or creates a new SKU entry. "
    "Returns the previous and updated stock levels. Quantity must be >= 0 (422 otherwise).",
    response_description="Previous and current stock levels for the SKU",
)
async def restock_inventory(request: Request, body: StockUpdate) -> StockResponse:
    """Increases available quantity for a SKU or creates a new SKU entry.

    Returns previous and updated stock levels. Quantity must be >= 0 (422 on validation failure).
    """

    previous = await INVENTORY.check_stock(body.sku)
    new_level = await INVENTORY.add_stock(body.sku, body.quantity)
    return StockResponse(
        sku=body.sku, previous_quantity=previous, current_quantity=new_level
    )


@ROUTER.get(
    "/inventory/{sku}",
    tags=["Inventory"],
    summary="Look up stock for a single SKU",
    description="Returns the current available quantity for the given SKU. "
    "Unknown SKUs return `quantity: 0` rather than an error.",
    response_description="SKU and its current available quantity",
)
async def get_single_sku(request: Request, sku: str) -> StockLevelResponse:
    """Returns the current available quantity for the given SKU.

    Always returns 200. Unknown SKUs return `quantity: 0`.
    """

    quantity = await INVENTORY.check_stock(sku)
    return StockLevelResponse(sku=sku, quantity=quantity)


@ROUTER.post(
    "/notify/order-confirmation",
    tags=["Notifications"],
    summary="Send an order confirmation notification",
    description="Dispatches an order confirmation to the specified recipient via email or SMS. "
    "The `channel` field must be `\"email\"` or `\"sms\"` — any other value returns 422.",
    response_description="Notification ID and delivery status",
    responses={
        422: {"description": "Invalid channel — must be 'email' or 'sms'"},
    },
)
async def send_order_notification(
    request: Request, body: OrderNotificationRequest
) -> NotificationResponse:
    """Dispatches an order confirmation to the specified recipient via email or SMS.

    The `channel` field must be `"email"` or `"sms"`. Any other value returns 422.
    Use the `reservation_id` from a prior `POST /order-intake` response.
    """

    notification_id = await NOTIFIER.send_order_confirmation(
        recipient=body.recipient,
        channel=body.channel,
        reservation_id=body.reservation_id,
        items=body.items,
    )
    return NotificationResponse(
        notification_id=notification_id,
        status="sent",
        channel=body.channel,
        event_type="order_confirmation",
    )


@ROUTER.post(
    "/notify/shipping-confirmation",
    tags=["Notifications"],
    summary="Send a shipping confirmation notification",
    description="Dispatches a shipping confirmation (with tracking number) to the specified "
    "recipient via email or SMS. The `channel` field must be `\"email\"` or `\"sms\"` — "
    "any other value returns 422.",
    response_description="Notification ID and delivery status",
    responses={
        422: {"description": "Invalid channel — must be 'email' or 'sms'"},
    },
)
async def send_shipping_notification(
    request: Request, body: ShippingNotificationRequest
) -> NotificationResponse:
    """Dispatches a shipping confirmation with tracking number via email or SMS.

    The `channel` field must be `"email"` or `"sms"`. Any other value returns 422.
    Use the `reservation_id` from a prior `POST /order-intake` response.
    """

    notification_id = await NOTIFIER.send_shipping_confirmation(
        recipient=body.recipient,
        channel=body.channel,
        reservation_id=body.reservation_id,
        tracking_number=body.tracking_number,
    )
    return NotificationResponse(
        notification_id=notification_id,
        status="sent",
        channel=body.channel,
        event_type="shipping_confirmation",
    )


@ROUTER.get(
    "/notify/log",
    tags=["Diagnostics"],
    summary="Get all sent notifications",
    description="Returns every notification dispatched since the application started, with a total count.",
    response_description="List of notification records and total count",
)
async def get_notification_log(request: Request) -> NotificationLogResponse:
    """Returns every notification dispatched since the application started."""

    notifications = await NOTIFIER.get_notification_log()
    return NotificationLogResponse(
        notifications=notifications, count=len(notifications)
    )


@ROUTER.get(
    "/analytics/summary",
    tags=["Diagnostics"],
    summary="Aggregate order analytics",
    description="Returns total order counts, acceptance/rejection breakdowns, and overall acceptance rate.",
    response_description="Aggregate order counts and acceptance rate",
)
async def analytics_summary(request: Request) -> AnalyticsSummaryResponse:
    """Returns total order counts, acceptance/rejection breakdowns, and overall acceptance rate."""

    return await TRACKER.get_summary()


@ROUTER.get(
    "/analytics/top-skus",
    tags=["Diagnostics"],
    summary="Most-requested SKUs",
    description="Returns the most-requested SKUs ranked by total requested quantity.",
    response_description="Ranked list of SKUs with request totals",
)
async def analytics_top_skus(
    request: Request,
    limit: int = Query(default=5, description="Maximum number of SKUs to return", ge=1),
) -> TopSkusResponse:
    """Returns the most-requested SKUs ranked by total requested quantity."""

    top = await TRACKER.get_top_skus(limit)
    return TopSkusResponse(top_skus=top, limit=limit)


@ROUTER.get(
    "/analytics/trend",
    tags=["Diagnostics"],
    summary="Hourly order trend",
    description="Returns order counts bucketed by hour for the specified lookback window.",
    response_description="Hourly order count buckets",
)
async def analytics_trend(
    request: Request,
    hours: int = Query(default=24, description="Number of hours to look back", ge=1),
) -> HourlyTrendResponse:
    """Returns order counts bucketed by hour for the specified lookback window."""

    trend = await TRACKER.get_hourly_trend(hours)
    return HourlyTrendResponse(hours_requested=hours, trend=trend)


@ROUTER.get(
    "/analytics/log",
    tags=["Diagnostics"],
    summary="Raw analytics event log",
    description="Returns every analytics event recorded since the application started, with a total count.",
    response_description="List of analytics events and total count",
)
async def analytics_log(request: Request) -> AnalyticsLogResponse:
    """Returns every analytics event recorded since the application started."""

    events = await TRACKER.get_event_log()
    return AnalyticsLogResponse(events=events, count=len(events))


@ROUTER.get(
    "/retry-queue/pending",
    tags=["Diagnostics"],
    summary="Pending retry tasks",
    description="Returns all tasks currently queued for retry (not yet exhausted or succeeded).",
    response_description="List of pending retry tasks",
)
async def retry_queue_pending(request: Request) -> list[RetryTaskResponse]:
    """Returns all tasks currently queued for retry."""

    return await RETRY_QUEUE.get_pending()


@ROUTER.get(
    "/retry-queue/dead-letters",
    tags=["Diagnostics"],
    summary="Dead-letter retry tasks",
    description="Returns all tasks that have exhausted their retry attempts and been moved to the dead-letter queue.",
    response_description="List of permanently failed retry tasks",
)
async def retry_queue_dead_letters(request: Request) -> list[RetryTaskResponse]:
    """Returns all tasks that exhausted retries and moved to the dead-letter queue."""

    return await RETRY_QUEUE.get_dead_letters()


@ROUTER.get(
    "/saga/all",
    tags=["Diagnostics"],
    summary="All tracked sagas",
    description="Returns every saga (order lifecycle) tracked since the application started, "
    "including current status and full transition history.",
    response_description="List of all sagas with status and history",
)
async def get_all_sagas(request: Request) -> SagaListResponse:
    """Returns every saga tracked since the application started, with full transition history."""

    sagas = await SAGA_TRACKER.get_all_sagas()
    details = [
        SagaDetailResponse(
            reservation_id=s["reservation_id"],
            current_status=s["status"],
            created_at=s["created_at"],
            updated_at=s["updated_at"],
            history=s["history"],
            order_count=len(s["orders"]),
        )
        for s in sagas
    ]
    return SagaListResponse(sagas=details, count=len(details))


@ROUTER.get(
    "/saga/{reservation_id}",
    tags=["Orders"],
    summary="Get saga state for a reservation",
    description="Returns the current saga state and full transition history for the given "
    "reservation ID. Use the `reservation_id` returned by `POST /order-intake`.",
    response_description="Saga details with current status and transition history",
    responses={
        404: {"description": "Reservation not found"},
    },
)
async def get_saga(
    request: Request, reservation_id: str, response: Response
) -> SagaDetailResponse:
    """Returns the saga state and full transition history for a reservation.

    Use the `reservation_id` from a prior `POST /order-intake` response.

    - **200** — Saga found; returns current status and history.
    - **404** — No saga exists for the given reservation ID.
    """

    saga = await SAGA_TRACKER.get_saga(reservation_id)
    if saga is None:
        response.status_code = 404
        return SagaDetailResponse(
            reservation_id=reservation_id,
            current_status="not_found",
            created_at="",
            updated_at="",
            history=[],
            order_count=0,
        )
    return SagaDetailResponse(
        reservation_id=saga["reservation_id"],
        current_status=saga["status"],
        created_at=saga["created_at"],
        updated_at=saga["updated_at"],
        history=saga["history"],
        order_count=len(saga["orders"]),
    )
