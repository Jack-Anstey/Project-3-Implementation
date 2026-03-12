# Third Party Imports
from fastapi import APIRouter, Response
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
@ROUTER.get("/health", tags=["Health"])
async def root_status(request: Request) -> BasicResponse:
    """Get the status of the FastAPI application

    Args:
        request (Request): The HTTP request

    Returns:
        BasicResponse: A basic response of Hello World!
    """

    logger.info("Logging works for the root endpoint")
    return BasicResponse(response="Healthy!")


@ROUTER.post("/order-intake", tags=["Orders"])
async def take_order(
    request: Request,
    orders: list[Order],
    response: Response,
) -> OrderResponse:
    """Take in an order and validate against available inventory

    Args:
        request (Request): The HTTP request
        orders (list[Order]): A list of `Orders` that comprise a user's total order
        response (Response): FastAPI response object for setting status code

    Returns:
        OrderResponse: Validation results with accepted/rejected items and reservation info
    """

    # Idempotency check
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key:
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


@ROUTER.get("/inventory", tags=["Diagnostics"])
async def get_inventory(request: Request) -> dict[str, int]:
    """Get current stock levels (diagnostic endpoint)

    Args:
        request (Request): The HTTP request

    Returns:
        dict[str, int]: Current stock levels by SKU
    """

    return await INVENTORY.get_all_stock()


@ROUTER.post("/inventory/confirm", tags=["Inventory"])
async def confirm_reservation(
    request: Request, body: ReservationAction, response: Response
) -> ReservationResponse:
    """Confirm a soft reservation after successful payment

    Args:
        request (Request): The HTTP request
        body (ReservationAction): Contains the reservation_id to confirm
        response (Response): FastAPI response object for setting status code

    Returns:
        ReservationResponse: Confirmation result with status
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


@ROUTER.post("/inventory/release", tags=["Inventory"])
async def release_reservation(
    request: Request, body: ReservationAction, response: Response
) -> ReservationResponse:
    """Release a soft reservation (payment failed or timed out)

    Args:
        request (Request): The HTTP request
        body (ReservationAction): Contains the reservation_id to release
        response (Response): FastAPI response object for setting status code

    Returns:
        ReservationResponse: Release result with status
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


@ROUTER.post("/inventory/restock", tags=["Diagnostics"])
async def restock_inventory(request: Request, body: StockUpdate) -> StockResponse:
    """Add stock to an existing or new SKU

    Args:
        request (Request): The HTTP request
        body (StockUpdate): Contains the SKU and quantity to add

    Returns:
        StockResponse: Before and after stock levels
    """

    previous = await INVENTORY.check_stock(body.sku)
    new_level = await INVENTORY.add_stock(body.sku, body.quantity)
    return StockResponse(
        sku=body.sku, previous_quantity=previous, current_quantity=new_level
    )


@ROUTER.get("/inventory/{sku}", tags=["Inventory"])
async def get_single_sku(request: Request, sku: str) -> StockLevelResponse:
    """Look up current stock for a single SKU

    Args:
        request (Request): The HTTP request
        sku (str): The SKU to look up

    Returns:
        StockLevelResponse: Current quantity for the SKU
    """

    quantity = await INVENTORY.check_stock(sku)
    return StockLevelResponse(sku=sku, quantity=quantity)


@ROUTER.post("/notify/order-confirmation", tags=["Notifications"])
async def send_order_notification(
    request: Request, body: OrderNotificationRequest
) -> NotificationResponse:
    """Send an order confirmation notification

    Args:
        request (Request): The HTTP request
        body (OrderNotificationRequest): Notification details

    Returns:
        NotificationResponse: Result with notification_id and status
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


@ROUTER.post("/notify/shipping-confirmation", tags=["Notifications"])
async def send_shipping_notification(
    request: Request, body: ShippingNotificationRequest
) -> NotificationResponse:
    """Send a shipping confirmation notification

    Args:
        request (Request): The HTTP request
        body (ShippingNotificationRequest): Notification details

    Returns:
        NotificationResponse: Result with notification_id and status
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


@ROUTER.get("/notify/log", tags=["Diagnostics"])
async def get_notification_log(request: Request) -> NotificationLogResponse:
    """Get all sent notifications (diagnostic endpoint)

    Args:
        request (Request): The HTTP request

    Returns:
        NotificationLogResponse: All notification records with count
    """

    notifications = await NOTIFIER.get_notification_log()
    return NotificationLogResponse(
        notifications=notifications, count=len(notifications)
    )


@ROUTER.get("/analytics/summary", tags=["Diagnostics"])
async def analytics_summary(request: Request) -> AnalyticsSummaryResponse:
    """Get aggregate order analytics summary

    Args:
        request (Request): The HTTP request

    Returns:
        AnalyticsSummaryResponse: Aggregate counts and acceptance rate
    """

    data = await TRACKER.get_summary()
    return AnalyticsSummaryResponse(**data)


@ROUTER.get("/analytics/top-skus", tags=["Diagnostics"])
async def analytics_top_skus(
    request: Request, limit: int = 5
) -> TopSkusResponse:
    """Get most-requested SKUs ranked by total quantity

    Args:
        request (Request): The HTTP request
        limit (int): Maximum number of SKUs to return (default 5)

    Returns:
        TopSkusResponse: Ranked list of SKUs
    """

    top = await TRACKER.get_top_skus(limit)
    return TopSkusResponse(top_skus=top, limit=limit)


@ROUTER.get("/analytics/trend", tags=["Diagnostics"])
async def analytics_trend(
    request: Request, hours: int = 24
) -> HourlyTrendResponse:
    """Get order counts bucketed by hour

    Args:
        request (Request): The HTTP request
        hours (int): Number of hours to look back (default 24)

    Returns:
        HourlyTrendResponse: Hourly order count buckets
    """

    trend = await TRACKER.get_hourly_trend(hours)
    return HourlyTrendResponse(hours_requested=hours, trend=trend)


@ROUTER.get("/analytics/log", tags=["Diagnostics"])
async def analytics_log(request: Request) -> AnalyticsLogResponse:
    """Get raw analytics event log (diagnostic endpoint)

    Args:
        request (Request): The HTTP request

    Returns:
        AnalyticsLogResponse: All recorded order events with count
    """

    events = await TRACKER.get_event_log()
    return AnalyticsLogResponse(events=events, count=len(events))


@ROUTER.get("/retry-queue/pending", tags=["Diagnostics"])
async def retry_queue_pending(request: Request) -> list[RetryTaskResponse]:
    """Get all pending retry tasks (diagnostic endpoint)

    Args:
        request (Request): The HTTP request

    Returns:
        list[RetryTaskResponse]: All pending retry tasks
    """

    pending = await RETRY_QUEUE.get_pending()
    return [RetryTaskResponse(**task) for task in pending]


@ROUTER.get("/retry-queue/dead-letters", tags=["Diagnostics"])
async def retry_queue_dead_letters(request: Request) -> list[RetryTaskResponse]:
    """Get all dead-letter retry tasks (diagnostic endpoint)

    Args:
        request (Request): The HTTP request

    Returns:
        list[RetryTaskResponse]: All permanently failed tasks
    """

    dead_letters = await RETRY_QUEUE.get_dead_letters()
    return [RetryTaskResponse(**task) for task in dead_letters]


@ROUTER.get("/saga/all", tags=["Diagnostics"])
async def get_all_sagas(request: Request) -> SagaListResponse:
    """Get all tracked sagas (diagnostic endpoint)

    Args:
        request (Request): The HTTP request

    Returns:
        SagaListResponse: All sagas with their current status and history
    """

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


@ROUTER.get("/saga/{reservation_id}", tags=["Orders"])
async def get_saga(
    request: Request, reservation_id: str, response: Response
) -> SagaDetailResponse:
    """Get the saga state and history for a reservation

    Args:
        request (Request): The HTTP request
        reservation_id (str): The reservation UUID to look up
        response (Response): FastAPI response object for setting status code

    Returns:
        SagaDetailResponse: Saga details with full transition history
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
