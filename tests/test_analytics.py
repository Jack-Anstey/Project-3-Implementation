import asyncio
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from src.scripts.analytics import InMemoryAnalytics
from src.scripts.responses import OrderResponse, OrderItemResult


# --- Helpers ---


def _make_order_response(accepted=None, rejected=None, reservation_id="res-1"):
    """Build a minimal OrderResponse for testing"""
    return OrderResponse(
        summary="test",
        reservation_id=reservation_id,
        expires_at=datetime.now(timezone.utc).isoformat(),
        accepted_items=accepted or [],
        rejected_items=rejected or [],
    )


def _make_item(sku="ABC123", obj="Widget", requested=10, accepted_qty=10, accepted=True, reason=None):
    return OrderItemResult(
        sku=sku,
        object=obj,
        requested_quantity=requested,
        accepted=accepted,
        accepted_quantity=accepted_qty,
        reason=reason,
    )


class _FakeOrder:
    """Minimal stand-in for Order input objects"""
    def __init__(self, sku, quantity):
        self.sku = sku
        self.quantity = quantity


# --- Unit tests (InMemoryAnalytics directly) ---


@pytest.fixture
def tracker():
    return InMemoryAnalytics()


@pytest.mark.asyncio
async def test_record_order_stores_event(tracker):
    resp = _make_order_response(accepted=[_make_item()])
    orders = [_FakeOrder("ABC123", 10)]
    await tracker.record_order(resp, 200, orders)
    log = await tracker.get_event_log()
    assert len(log) == 1
    event = log[0]
    assert event["reservation_id"] == "res-1"
    assert event["status"] == "accepted"
    assert event["status_code"] == 200
    assert "timestamp" in event
    assert len(event["skus"]) == 1


@pytest.mark.asyncio
async def test_record_order_status_mapping(tracker):
    resp_200 = _make_order_response(accepted=[_make_item()], reservation_id="r-200")
    resp_207 = _make_order_response(
        accepted=[_make_item()],
        rejected=[_make_item(sku="BAD", accepted=False, accepted_qty=0)],
        reservation_id="r-207",
    )
    resp_409 = _make_order_response(
        rejected=[_make_item(sku="BAD", accepted=False, accepted_qty=0)],
        reservation_id="r-409",
    )

    await tracker.record_order(resp_200, 200, [_FakeOrder("ABC123", 10)])
    await tracker.record_order(resp_207, 207, [_FakeOrder("ABC123", 10), _FakeOrder("BAD", 5)])
    await tracker.record_order(resp_409, 409, [_FakeOrder("BAD", 5)])

    log = await tracker.get_event_log()
    assert log[0]["status"] == "accepted"
    assert log[1]["status"] == "partial"
    assert log[2]["status"] == "rejected"


@pytest.mark.asyncio
async def test_summary_empty(tracker):
    summary = await tracker.get_summary()
    assert summary["total_orders"] == 0
    assert summary["accepted"] == 0
    assert summary["partial"] == 0
    assert summary["rejected"] == 0
    assert summary["acceptance_rate"] == 0.0


@pytest.mark.asyncio
async def test_summary_counts(tracker):
    await tracker.record_order(
        _make_order_response(accepted=[_make_item()], reservation_id="r1"),
        200, [_FakeOrder("ABC123", 10)]
    )
    await tracker.record_order(
        _make_order_response(
            accepted=[_make_item()],
            rejected=[_make_item(sku="X", accepted=False, accepted_qty=0)],
            reservation_id="r2",
        ),
        207, [_FakeOrder("ABC123", 10), _FakeOrder("X", 5)]
    )
    await tracker.record_order(
        _make_order_response(
            rejected=[_make_item(sku="X", accepted=False, accepted_qty=0)],
            reservation_id="r3",
        ),
        409, [_FakeOrder("X", 5)]
    )

    summary = await tracker.get_summary()
    assert summary["total_orders"] == 3
    assert summary["accepted"] == 1
    assert summary["partial"] == 1
    assert summary["rejected"] == 1


@pytest.mark.asyncio
async def test_summary_acceptance_rate(tracker):
    # 2 accepted, 1 rejected → 2/3 ≈ 0.6667
    for i in range(2):
        await tracker.record_order(
            _make_order_response(accepted=[_make_item()], reservation_id=f"a-{i}"),
            200, [_FakeOrder("ABC123", 1)]
        )
    await tracker.record_order(
        _make_order_response(
            rejected=[_make_item(sku="X", accepted=False, accepted_qty=0)],
            reservation_id="r-1",
        ),
        409, [_FakeOrder("X", 1)]
    )

    summary = await tracker.get_summary()
    assert abs(summary["acceptance_rate"] - 0.6667) < 0.001


@pytest.mark.asyncio
async def test_top_skus_ranking(tracker):
    # ABC123 requested 100, DEF456 requested 50
    await tracker.record_order(
        _make_order_response(accepted=[_make_item(sku="ABC123", requested=100, accepted_qty=100)], reservation_id="r1"),
        200, [_FakeOrder("ABC123", 100)]
    )
    await tracker.record_order(
        _make_order_response(accepted=[_make_item(sku="DEF456", requested=50, accepted_qty=50)], reservation_id="r2"),
        200, [_FakeOrder("DEF456", 50)]
    )

    top = await tracker.get_top_skus(10)
    assert top[0]["sku"] == "ABC123"
    assert top[0]["total_requested"] == 100
    assert top[1]["sku"] == "DEF456"


@pytest.mark.asyncio
async def test_top_skus_limit(tracker):
    for i in range(5):
        sku = f"SKU-{i}"
        await tracker.record_order(
            _make_order_response(
                accepted=[_make_item(sku=sku, requested=i + 1, accepted_qty=i + 1)],
                reservation_id=f"r-{i}",
            ),
            200, [_FakeOrder(sku, i + 1)]
        )

    top = await tracker.get_top_skus(2)
    assert len(top) == 2


@pytest.mark.asyncio
async def test_hourly_trend_buckets(tracker):
    await tracker.record_order(
        _make_order_response(accepted=[_make_item()], reservation_id="r1"),
        200, [_FakeOrder("ABC123", 1)]
    )
    await tracker.record_order(
        _make_order_response(accepted=[_make_item()], reservation_id="r2"),
        200, [_FakeOrder("ABC123", 1)]
    )

    trend = await tracker.get_hourly_trend(1)
    assert len(trend) >= 1
    total_count = sum(b["order_count"] for b in trend)
    assert total_count == 2


@pytest.mark.asyncio
async def test_hourly_trend_cutoff(tracker):
    # Record an event, then manually backdate it
    await tracker.record_order(
        _make_order_response(accepted=[_make_item()], reservation_id="old"),
        200, [_FakeOrder("ABC123", 1)]
    )
    # Backdate the event to 48 hours ago
    tracker._events[0]["timestamp"] = (
        datetime.now(timezone.utc) - timedelta(hours=48)
    ).isoformat()

    # Record a recent event
    await tracker.record_order(
        _make_order_response(accepted=[_make_item()], reservation_id="new"),
        200, [_FakeOrder("ABC123", 1)]
    )

    trend = await tracker.get_hourly_trend(24)
    total_count = sum(b["order_count"] for b in trend)
    assert total_count == 1  # Only the recent one


@pytest.mark.asyncio
async def test_event_log_returns_all(tracker):
    for i in range(5):
        await tracker.record_order(
            _make_order_response(accepted=[_make_item()], reservation_id=f"r-{i}"),
            200, [_FakeOrder("ABC123", 1)]
        )
    log = await tracker.get_event_log()
    assert len(log) == 5


@pytest.mark.asyncio
async def test_concurrent_recording(tracker):
    """Multiple concurrent records should not lose any entries"""

    async def record(i):
        await tracker.record_order(
            _make_order_response(accepted=[_make_item()], reservation_id=f"r-{i}"),
            200, [_FakeOrder("ABC123", 1)]
        )

    await asyncio.gather(*[record(i) for i in range(20)])
    log = await tracker.get_event_log()
    assert len(log) == 20


# --- Integration tests (FastAPI TestClient) ---


@pytest.fixture
def client():
    from src.app import start_app
    from src.scripts.inventory import InMemoryInventory
    from src.scripts.notifications import MockNotificationService
    from src.scripts.analytics import InMemoryAnalytics
    from src.scripts.retry_queue import InMemoryRetryQueue
    from src.scripts.idempotency import InMemoryIdempotencyStore
    from src.scripts.saga import InMemorySagaTracker
    import src.scripts.router as router_module
    import src.scripts.inventory as inv_module
    import src.scripts.notifications as notif_module
    import src.scripts.analytics as analytics_module
    import src.scripts.retry_queue as retry_module
    import src.scripts.idempotency as idemp_module
    import src.scripts.saga as saga_module

    # Reset globals to fresh instances for each test
    fresh_inv = InMemoryInventory()
    inv_module.INVENTORY = fresh_inv
    router_module.INVENTORY = fresh_inv

    fresh_notifier = MockNotificationService()
    notif_module.NOTIFIER = fresh_notifier
    router_module.NOTIFIER = fresh_notifier

    fresh_tracker = InMemoryAnalytics()
    analytics_module.TRACKER = fresh_tracker
    router_module.TRACKER = fresh_tracker

    fresh_queue = InMemoryRetryQueue()
    fresh_queue.register_handler("send_order_confirmation", fresh_notifier.send_order_confirmation)
    fresh_queue.register_handler("record_order", fresh_tracker.record_order)
    retry_module.RETRY_QUEUE = fresh_queue
    router_module.RETRY_QUEUE = fresh_queue

    fresh_idemp = InMemoryIdempotencyStore()
    idemp_module.IDEMPOTENCY_STORE = fresh_idemp
    router_module.IDEMPOTENCY_STORE = fresh_idemp

    fresh_saga = InMemorySagaTracker()
    saga_module.SAGA_TRACKER = fresh_saga
    router_module.SAGA_TRACKER = fresh_saga

    # Wire up fresh event bus
    import src.scripts.event_handlers as handlers_module
    import src.scripts.event_bus as bus_module
    from src.scripts.event_bus import EventBus
    from src.scripts.event_handlers import register_all

    fresh_bus = EventBus()
    bus_module.EVENT_BUS = fresh_bus
    router_module.EVENT_BUS = fresh_bus

    handlers_module.SAGA_TRACKER = fresh_saga
    handlers_module.RETRY_QUEUE = fresh_queue
    handlers_module.IDEMPOTENCY_STORE = fresh_idemp

    register_all(fresh_bus)

    app = start_app(root_path="/order-processing", _run=False)
    return TestClient(app)


def test_analytics_summary_endpoint(client):
    resp = client.get("/order-processing/analytics/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_orders" in data
    assert "accepted" in data
    assert "acceptance_rate" in data
    assert data["total_orders"] == 0


def test_analytics_top_skus_endpoint(client):
    resp = client.get("/order-processing/analytics/top-skus?limit=3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["limit"] == 3
    assert "top_skus" in data


def test_analytics_trend_endpoint(client):
    resp = client.get("/order-processing/analytics/trend?hours=12")
    assert resp.status_code == 200
    data = resp.json()
    assert data["hours_requested"] == 12
    assert "trend" in data


def test_analytics_log_endpoint(client):
    resp = client.get("/order-processing/analytics/log")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["events"] == []


def _process_retry_queue(client):
    """Helper: process pending tasks in the retry queue so side effects fire"""
    import asyncio
    import src.scripts.router as router_module
    asyncio.get_event_loop().run_until_complete(router_module.RETRY_QUEUE.process_pending())


def test_autorecord_on_accepted_order(client):
    resp = client.post(
        "/order-processing/order-intake",
        json=[{"object": "Widget", "sku": "ABC123", "quantity": 2}],
    )
    assert resp.status_code == 200
    _process_retry_queue(client)
    log = client.get("/order-processing/analytics/log").json()
    assert log["count"] == 1
    assert log["events"][0]["status"] == "accepted"


def test_autorecord_on_rejected_order(client):
    resp = client.post(
        "/order-processing/order-intake",
        json=[{"object": "Ghost", "sku": "NONEXISTENT", "quantity": 1}],
    )
    assert resp.status_code == 409
    _process_retry_queue(client)
    log = client.get("/order-processing/analytics/log").json()
    assert log["count"] == 1
    assert log["events"][0]["status"] == "rejected"


def test_autorecord_on_partial_order(client):
    resp = client.post(
        "/order-processing/order-intake",
        json=[
            {"object": "Widget", "sku": "ABC123", "quantity": 2},
            {"object": "Ghost", "sku": "NONEXISTENT", "quantity": 1},
        ],
    )
    assert resp.status_code == 207
    _process_retry_queue(client)
    summary = client.get("/order-processing/analytics/summary").json()
    assert summary["partial"] == 1


def test_analytics_sku_tracking(client):
    client.post(
        "/order-processing/order-intake",
        json=[
            {"object": "Widget", "sku": "ABC123", "quantity": 5},
            {"object": "Gadget", "sku": "DEF456", "quantity": 3},
        ],
    )
    _process_retry_queue(client)
    top = client.get("/order-processing/analytics/top-skus?limit=10").json()
    skus = [entry["sku"] for entry in top["top_skus"]]
    assert "ABC123" in skus
    assert "DEF456" in skus
    # ABC123 requested more, should be first
    assert top["top_skus"][0]["sku"] == "ABC123"
