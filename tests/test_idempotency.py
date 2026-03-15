import asyncio
import pytest
from fastapi.testclient import TestClient
from src.scripts.idempotency import InMemoryIdempotencyStore


@pytest.fixture
def store():
    return InMemoryIdempotencyStore()


@pytest.mark.asyncio
async def test_put_and_get(store):
    await store.put("key1", {"data": "value"}, 200, ttl_seconds=60)
    result = await store.get("key1")
    assert result is not None
    assert result["response"] == {"data": "value"}
    assert result["status_code"] == 200


@pytest.mark.asyncio
async def test_get_expired_returns_none(store):
    await store.put("key-exp", {"data": "old"}, 200, ttl_seconds=0)
    await asyncio.sleep(0.01)
    result = await store.get("key-exp")
    assert result is None


@pytest.mark.asyncio
async def test_get_unknown_key_returns_none(store):
    result = await store.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_cache_cap_evicts_oldest():
    store = InMemoryIdempotencyStore(max_size=3)
    for i in range(5):
        await store.put(f"key-{i}", {"i": i}, 200, ttl_seconds=60)
    # Only 3 should remain; oldest (key-0, key-1) evicted
    assert await store.get("key-0") is None
    assert await store.get("key-1") is None
    assert await store.get("key-4") is not None


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


def test_idempotent_order_returns_cached(client):
    headers = {"Idempotency-Key": "test-key-1"}
    resp1 = client.post(
        "/order-processing/order-intake",
        json=[{"object": "Widget", "sku": "ABC123", "quantity": 2}],
        headers=headers,
    )
    assert resp1.status_code == 200
    rid1 = resp1.json()["reservation_id"]

    resp2 = client.post(
        "/order-processing/order-intake",
        json=[{"object": "Widget", "sku": "ABC123", "quantity": 2}],
        headers=headers,
    )
    assert resp2.status_code == 200
    rid2 = resp2.json()["reservation_id"]
    assert rid1 == rid2


def test_different_key_creates_new_order(client):
    resp1 = client.post(
        "/order-processing/order-intake",
        json=[{"object": "Widget", "sku": "ABC123", "quantity": 1}],
        headers={"Idempotency-Key": "key-a"},
    )
    resp2 = client.post(
        "/order-processing/order-intake",
        json=[{"object": "Widget", "sku": "ABC123", "quantity": 1}],
        headers={"Idempotency-Key": "key-b"},
    )
    assert resp1.json()["reservation_id"] != resp2.json()["reservation_id"]
