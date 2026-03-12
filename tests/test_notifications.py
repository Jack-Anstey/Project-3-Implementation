import asyncio
import uuid
import pytest
from fastapi.testclient import TestClient
from src.scripts.notifications import MockNotificationService


# --- Unit tests (MockNotificationService directly) ---


@pytest.fixture
def service():
    return MockNotificationService()


@pytest.mark.asyncio
async def test_send_order_confirmation_returns_id(service):
    nid = await service.send_order_confirmation(
        recipient="user@example.com",
        channel="email",
        reservation_id="res-1",
        items=[{"sku": "ABC123", "quantity": 2}],
    )
    # Should be a valid UUID
    uuid.UUID(nid)


@pytest.mark.asyncio
async def test_send_shipping_confirmation_returns_id(service):
    nid = await service.send_shipping_confirmation(
        recipient="user@example.com",
        channel="sms",
        reservation_id="res-2",
        tracking_number="TRACK-001",
    )
    uuid.UUID(nid)


@pytest.mark.asyncio
async def test_notification_log_records_order(service):
    await service.send_order_confirmation(
        recipient="a@b.com", channel="email", reservation_id="r1", items=[]
    )
    log = await service.get_notification_log()
    assert len(log) == 1
    assert log[0]["event_type"] == "order_confirmation"


@pytest.mark.asyncio
async def test_notification_log_records_shipping(service):
    await service.send_shipping_confirmation(
        recipient="a@b.com", channel="sms", reservation_id="r2", tracking_number="T1"
    )
    log = await service.get_notification_log()
    assert len(log) == 1
    assert log[0]["event_type"] == "shipping_confirmation"


@pytest.mark.asyncio
async def test_notification_log_multiple(service):
    await service.send_order_confirmation("a@b.com", "email", "r1", [])
    await service.send_shipping_confirmation("a@b.com", "sms", "r2", "T1")
    await service.send_order_confirmation("c@d.com", "sms", "r3", [{"sku": "X"}])
    log = await service.get_notification_log()
    assert len(log) == 3


@pytest.mark.asyncio
async def test_notification_log_content(service):
    await service.send_order_confirmation(
        recipient="test@example.com",
        channel="email",
        reservation_id="res-content",
        items=[{"sku": "ABC123", "quantity": 5}],
    )
    log = await service.get_notification_log()
    record = log[0]
    assert record["recipient"] == "test@example.com"
    assert record["channel"] == "email"
    assert record["payload"]["reservation_id"] == "res-content"
    assert record["payload"]["items"] == [{"sku": "ABC123", "quantity": 5}]
    assert "sent_at" in record


@pytest.mark.asyncio
async def test_concurrent_notifications(service):
    """Multiple concurrent sends should not lose any entries"""

    async def send(i):
        return await service.send_order_confirmation(
            f"user{i}@test.com", "email", f"res-{i}", []
        )

    results = await asyncio.gather(*[send(i) for i in range(20)])
    assert len(results) == 20
    log = await service.get_notification_log()
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


def test_order_notification_endpoint(client):
    resp = client.post(
        "/order-processing/notify/order-confirmation",
        json={
            "recipient": "user@example.com",
            "channel": "email",
            "reservation_id": "res-endpoint",
            "items": [{"sku": "ABC123", "quantity": 1}],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "notification_id" in data
    assert data["status"] == "sent"
    assert data["event_type"] == "order_confirmation"


def test_shipping_notification_endpoint(client):
    resp = client.post(
        "/order-processing/notify/shipping-confirmation",
        json={
            "recipient": "user@example.com",
            "channel": "sms",
            "reservation_id": "res-ship",
            "tracking_number": "TRACK-999",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "notification_id" in data
    assert data["status"] == "sent"
    assert data["event_type"] == "shipping_confirmation"


def test_notification_log_endpoint(client):
    # Send a notification first
    client.post(
        "/order-processing/notify/order-confirmation",
        json={
            "recipient": "log@test.com",
            "channel": "email",
            "reservation_id": "res-log",
            "items": [],
        },
    )
    # Check the log
    resp = client.get("/order-processing/notify/log")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert len(data["notifications"]) == 1


def test_invalid_channel_rejected(client):
    resp = client.post(
        "/order-processing/notify/order-confirmation",
        json={
            "recipient": "user@example.com",
            "channel": "carrier_pigeon",
            "reservation_id": "res-bad",
            "items": [],
        },
    )
    assert resp.status_code == 422


def _process_retry_queue(client):
    """Helper: process pending tasks in the retry queue so side effects fire"""
    import asyncio
    import src.scripts.router as router_module
    asyncio.get_event_loop().run_until_complete(router_module.RETRY_QUEUE.process_pending())


def test_autofire_on_successful_order(client):
    resp = client.post(
        "/order-processing/order-intake",
        json=[{"object": "Widget", "sku": "ABC123", "quantity": 2}],
    )
    assert resp.status_code == 200
    _process_retry_queue(client)
    log = client.get("/order-processing/notify/log").json()
    assert log["count"] == 1
    assert log["notifications"][0]["event_type"] == "order_confirmation"


def test_no_autofire_on_rejected_order(client):
    resp = client.post(
        "/order-processing/order-intake",
        json=[{"object": "Ghost", "sku": "NONEXISTENT", "quantity": 1}],
    )
    assert resp.status_code == 409
    _process_retry_queue(client)
    log = client.get("/order-processing/notify/log").json()
    assert log["count"] == 0


def test_autofire_on_partial_order(client):
    resp = client.post(
        "/order-processing/order-intake",
        json=[
            {"object": "Widget", "sku": "ABC123", "quantity": 2},
            {"object": "Ghost", "sku": "NONEXISTENT", "quantity": 1},
        ],
    )
    assert resp.status_code == 207
    _process_retry_queue(client)
    log = client.get("/order-processing/notify/log").json()
    assert log["count"] == 1
    assert log["notifications"][0]["event_type"] == "order_confirmation"
