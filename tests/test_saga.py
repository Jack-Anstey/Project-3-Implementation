import asyncio
import pytest
from fastapi.testclient import TestClient
from src.scripts.saga import InMemorySagaTracker, OrderSagaStatus


# --- Helpers ---


class _FakeOrder:
    """Minimal stand-in for Order input objects"""
    def __init__(self, sku, quantity):
        self.sku = sku
        self.quantity = quantity


# --- Unit tests (InMemorySagaTracker directly) ---


@pytest.fixture
def tracker():
    return InMemorySagaTracker()


@pytest.mark.asyncio
async def test_start_saga_creates_entry(tracker):
    orders = [_FakeOrder("ABC123", 5)]
    saga = await tracker.start_saga("res-1", orders, "key-1")
    assert saga["reservation_id"] == "res-1"
    assert saga["status"] == "RECEIVED"
    assert saga["idempotency_key"] == "key-1"
    assert len(saga["history"]) == 1
    assert saga["history"][0].status == "RECEIVED"
    assert len(saga["orders"]) == 1
    assert saga["orders"][0]["sku"] == "ABC123"


@pytest.mark.asyncio
async def test_transition_updates_status(tracker):
    await tracker.start_saga("res-2", [_FakeOrder("X", 1)])
    result = await tracker.transition("res-2", OrderSagaStatus.RESERVED)
    assert result is True
    saga = await tracker.get_saga("res-2")
    assert saga["status"] == "RESERVED"
    assert len(saga["history"]) == 2
    assert saga["history"][1].status == "RESERVED"


@pytest.mark.asyncio
async def test_transition_nonexistent_saga(tracker):
    result = await tracker.transition("ghost", OrderSagaStatus.RESERVED)
    assert result is False


@pytest.mark.asyncio
async def test_illegal_transition_skipped(tracker):
    await tracker.start_saga("res-3", [_FakeOrder("X", 1)])
    await tracker.transition("res-3", OrderSagaStatus.REJECTED)
    # REJECTED is terminal — cannot transition to EXPIRED
    result = await tracker.transition("res-3", OrderSagaStatus.EXPIRED)
    assert result is False
    saga = await tracker.get_saga("res-3")
    assert saga["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_get_saga_unknown_returns_none(tracker):
    result = await tracker.get_saga("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_all_sagas(tracker):
    await tracker.start_saga("res-a", [_FakeOrder("X", 1)])
    await tracker.start_saga("res-b", [_FakeOrder("Y", 2)])
    all_sagas = await tracker.get_all_sagas()
    assert len(all_sagas) == 2
    ids = {s["reservation_id"] for s in all_sagas}
    assert ids == {"res-a", "res-b"}


@pytest.mark.asyncio
async def test_saga_cap_eviction():
    tracker = InMemorySagaTracker(max_sagas=3)
    for i in range(5):
        await tracker.start_saga(f"res-{i}", [_FakeOrder("X", 1)])
    all_sagas = await tracker.get_all_sagas()
    assert len(all_sagas) == 3
    # Oldest two (res-0, res-1) should be evicted
    ids = {s["reservation_id"] for s in all_sagas}
    assert "res-0" not in ids
    assert "res-1" not in ids
    assert "res-4" in ids


@pytest.mark.asyncio
async def test_full_happy_path(tracker):
    await tracker.start_saga("happy", [_FakeOrder("ABC", 10)])
    await tracker.transition("happy", OrderSagaStatus.RESERVED)
    await tracker.transition("happy", OrderSagaStatus.NOTIFYING)
    await tracker.transition("happy", OrderSagaStatus.CONFIRMED)
    saga = await tracker.get_saga("happy")
    assert saga["status"] == "CONFIRMED"
    assert len(saga["history"]) == 4
    statuses = [h.status for h in saga["history"]]
    assert statuses == ["RECEIVED", "RESERVED", "NOTIFYING", "CONFIRMED"]


@pytest.mark.asyncio
async def test_compensation_path(tracker):
    await tracker.start_saga("comp", [_FakeOrder("ABC", 10)])
    await tracker.transition("comp", OrderSagaStatus.RESERVED)
    await tracker.transition("comp", OrderSagaStatus.COMPENSATING)
    await tracker.transition("comp", OrderSagaStatus.COMPENSATION_COMPLETE)
    saga = await tracker.get_saga("comp")
    assert saga["status"] == "COMPENSATION_COMPLETE"
    statuses = [h.status for h in saga["history"]]
    assert statuses == ["RECEIVED", "RESERVED", "COMPENSATING", "COMPENSATION_COMPLETE"]


@pytest.mark.asyncio
async def test_expired_path(tracker):
    await tracker.start_saga("exp", [_FakeOrder("ABC", 10)])
    await tracker.transition("exp", OrderSagaStatus.RESERVED)
    await tracker.transition("exp", OrderSagaStatus.EXPIRED)
    saga = await tracker.get_saga("exp")
    assert saga["status"] == "EXPIRED"
    statuses = [h.status for h in saga["history"]]
    assert statuses == ["RECEIVED", "RESERVED", "EXPIRED"]


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


def test_saga_endpoint_after_order(client):
    resp = client.post(
        "/order-processing/order-intake",
        json=[{"object": "Widget", "sku": "ABC123", "quantity": 2}],
    )
    assert resp.status_code == 200
    rid = resp.json()["reservation_id"]

    saga_resp = client.get(f"/order-processing/saga/{rid}")
    assert saga_resp.status_code == 200
    data = saga_resp.json()
    assert data["reservation_id"] == rid
    assert data["current_status"] == "NOTIFYING"
    assert data["order_count"] == 1
    statuses = [h["status"] for h in data["history"]]
    assert statuses == ["RECEIVED", "RESERVED", "NOTIFYING"]


def test_saga_endpoint_not_found(client):
    resp = client.get("/order-processing/saga/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["current_status"] == "not_found"


def test_saga_confirm_flow(client):
    resp = client.post(
        "/order-processing/order-intake",
        json=[{"object": "Widget", "sku": "ABC123", "quantity": 2}],
    )
    rid = resp.json()["reservation_id"]

    client.post(
        "/order-processing/inventory/confirm",
        json={"reservation_id": rid},
    )

    saga_resp = client.get(f"/order-processing/saga/{rid}")
    data = saga_resp.json()
    assert data["current_status"] == "CONFIRMED"
    statuses = [h["status"] for h in data["history"]]
    assert "CONFIRMED" in statuses


def test_saga_release_flow(client):
    resp = client.post(
        "/order-processing/order-intake",
        json=[{"object": "Widget", "sku": "ABC123", "quantity": 2}],
    )
    rid = resp.json()["reservation_id"]

    client.post(
        "/order-processing/inventory/release",
        json={"reservation_id": rid},
    )

    saga_resp = client.get(f"/order-processing/saga/{rid}")
    data = saga_resp.json()
    assert data["current_status"] == "COMPENSATION_COMPLETE"
    statuses = [h["status"] for h in data["history"]]
    assert "COMPENSATING" in statuses
    assert "COMPENSATION_COMPLETE" in statuses


def test_saga_rejected_order(client):
    resp = client.post(
        "/order-processing/order-intake",
        json=[{"object": "Ghost", "sku": "NONEXISTENT", "quantity": 1}],
    )
    assert resp.status_code == 409
    rid = resp.json()["reservation_id"]

    saga_resp = client.get(f"/order-processing/saga/{rid}")
    data = saga_resp.json()
    assert data["current_status"] == "REJECTED"
    statuses = [h["status"] for h in data["history"]]
    assert statuses == ["RECEIVED", "REJECTED"]


def test_saga_all_endpoint(client):
    client.post(
        "/order-processing/order-intake",
        json=[{"object": "Widget", "sku": "ABC123", "quantity": 1}],
    )
    client.post(
        "/order-processing/order-intake",
        json=[{"object": "Gadget", "sku": "DEF456", "quantity": 1}],
    )

    resp = client.get("/order-processing/saga/all")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert len(data["sagas"]) == 2
