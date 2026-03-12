# Native Imports
from pydantic import BaseModel


# Response Classes
class BasicResponse(BaseModel):
    """A simple pydantic class that enables simple returns for the router

    Args:
        response (str): The string contents of the `BasicResponse` object
    """

    response: str


class OrderItemResult(BaseModel):
    """Result for an individual item within an order

    Args:
        sku (str): The SKU of the item
        object (str): The name of the object
        requested_quantity (int): The quantity originally requested
        accepted (bool): Whether the item was accepted
        accepted_quantity (int): The quantity that was accepted
        reason (str, optional): Reason for rejection, if applicable
    """

    sku: str
    object: str
    requested_quantity: int
    accepted: bool
    accepted_quantity: int
    reason: str | None = None


class OrderResponse(BaseModel):
    """Response for the order-intake endpoint with stock validation results

    Args:
        summary (str): A human-readable summary of the order result
        reservation_id (str): UUID identifying the soft reservation
        expires_at (str): ISO 8601 timestamp when the reservation expires
        accepted_items (list[OrderItemResult]): Items that were fully or partially accepted
        rejected_items (list[OrderItemResult]): Items that were rejected
    """

    summary: str
    reservation_id: str
    expires_at: str
    accepted_items: list[OrderItemResult]
    rejected_items: list[OrderItemResult]


class ReservationResponse(BaseModel):
    """Response for confirm/release reservation endpoints

    Args:
        reservation_id (str): The reservation UUID acted on
        status (str): Result status (e.g. "confirmed", "released", "not_found")
    """

    reservation_id: str
    status: str


class StockResponse(BaseModel):
    """Response for restock endpoint showing before/after quantities

    Args:
        sku (str): The SKU that was restocked
        previous_quantity (int): Stock level before the restock
        current_quantity (int): Stock level after the restock
    """

    sku: str
    previous_quantity: int
    current_quantity: int


class StockLevelResponse(BaseModel):
    """Response for single SKU stock lookup

    Args:
        sku (str): The SKU queried
        quantity (int): Current available quantity
    """

    sku: str
    quantity: int


class NotificationResponse(BaseModel):
    """Response for notification send endpoints

    Args:
        notification_id (str): UUID identifying the sent notification
        status (str): Result status (e.g. "sent")
        channel (str): The channel used ("email" or "sms")
        event_type (str): The notification event type
    """

    notification_id: str
    status: str
    channel: str
    event_type: str


class NotificationLogResponse(BaseModel):
    """Response for the notification log diagnostic endpoint

    Args:
        notifications (list[dict]): All sent notification records
        count (int): Total number of notifications
    """

    notifications: list[dict]
    count: int


class AnalyticsSummaryResponse(BaseModel):
    """Aggregate order analytics summary

    Args:
        total_orders (int): Total number of orders recorded
        accepted (int): Orders where all items were accepted
        partial (int): Orders with mixed accepted/rejected items
        rejected (int): Orders where all items were rejected
        acceptance_rate (float): Ratio of fully accepted orders to total
        total_items_requested (int): Sum of all quantities requested
        total_items_accepted (int): Sum of all quantities accepted
        total_items_rejected (int): Sum of all quantities rejected
    """

    total_orders: int
    accepted: int
    partial: int
    rejected: int
    acceptance_rate: float
    total_items_requested: int
    total_items_accepted: int
    total_items_rejected: int


class TopSkuEntry(BaseModel):
    """A single SKU ranking entry

    Args:
        sku (str): The SKU identifier
        total_requested (int): Total quantity requested across all orders
    """

    sku: str
    total_requested: int


class TopSkusResponse(BaseModel):
    """Response for top SKUs analytics endpoint

    Args:
        top_skus (list[TopSkuEntry]): SKUs ranked by total requested quantity
        limit (int): The limit that was applied
    """

    top_skus: list[TopSkuEntry]
    limit: int


class HourlyTrendEntry(BaseModel):
    """A single hourly bucket in the trend

    Args:
        hour (str): ISO 8601 hour string (e.g. "2026-03-11T14:00:00+00:00")
        order_count (int): Number of orders in this hour
    """

    hour: str
    order_count: int


class HourlyTrendResponse(BaseModel):
    """Response for hourly trend analytics endpoint

    Args:
        hours_requested (int): The number of hours that were requested
        trend (list[HourlyTrendEntry]): Order counts bucketed by hour
    """

    hours_requested: int
    trend: list[HourlyTrendEntry]


class AnalyticsLogResponse(BaseModel):
    """Response for the analytics event log diagnostic endpoint

    Args:
        events (list[dict]): All recorded order events
        count (int): Total number of events
    """

    events: list[dict]
    count: int


class SagaTransitionEntry(BaseModel):
    """A single entry in a saga's transition history

    Args:
        status (str): The saga status at this point
        timestamp (str): ISO 8601 timestamp of the transition
        metadata (dict | None): Optional metadata associated with the transition
    """

    status: str
    timestamp: str
    metadata: dict | None = None


class SagaDetailResponse(BaseModel):
    """Detailed view of a single saga

    Args:
        reservation_id (str): The reservation UUID identifying this saga
        current_status (str): Current saga status
        created_at (str): ISO 8601 timestamp when the saga was created
        updated_at (str): ISO 8601 timestamp of the last transition
        history (list[SagaTransitionEntry]): Full transition history
        order_count (int): Number of orders in this saga
    """

    reservation_id: str
    current_status: str
    created_at: str
    updated_at: str
    history: list[SagaTransitionEntry]
    order_count: int


class SagaListResponse(BaseModel):
    """Response for listing all tracked sagas

    Args:
        sagas (list[SagaDetailResponse]): All tracked sagas
        count (int): Total number of sagas
    """

    sagas: list[SagaDetailResponse]
    count: int


class RetryTaskResponse(BaseModel):
    """A single retry task entry

    Args:
        task_id (str): UUID identifying the task
        callable_name (str): Name of the registered handler
        attempts (int): Number of attempts so far
        max_retries (int): Maximum retry attempts allowed
        next_retry_at (str | None): ISO 8601 timestamp of next retry
        created_at (str): ISO 8601 timestamp when the task was created
        last_error (str | None): Error message from the last failed attempt
    """

    task_id: str
    callable_name: str
    attempts: int
    max_retries: int
    next_retry_at: str | None = None
    created_at: str
    last_error: str | None = None


class RetryQueueStatusResponse(BaseModel):
    """Response for retry queue diagnostic endpoints

    Args:
        pending (list[RetryTaskResponse]): Tasks awaiting processing
        pending_count (int): Number of pending tasks
        dead_letters (list[RetryTaskResponse]): Permanently failed tasks
        dead_letter_count (int): Number of dead-letter tasks
    """

    pending: list[RetryTaskResponse]
    pending_count: int
    dead_letters: list[RetryTaskResponse]
    dead_letter_count: int
