import asyncio
import pytest
from fastapi.testclient import TestClient
from src.scripts.inputs import Order
from src.scripts.inventory import InMemoryInventory, validate_and_reserve


@pytest.fixture
def store():
    return InMemoryInventory()


@pytest.mark.asyncio
async def test_check_stock_known_sku(store):
    assert await store.check_stock("ABC123") == 500


@pytest.mark.asyncio
async def test_check_stock_unknown_sku(store):
    assert await store.check_stock("UNKNOWN") == 0


@pytest.mark.asyncio
async def test_soft_reserve_sufficient_stock(store):
    reserved = await store.soft_reserve("ABC123", 10, "res-1", ttl_seconds=60)
    assert reserved == 10
    assert await store.check_stock("ABC123") == 490


@pytest.mark.asyncio
async def test_soft_reserve_insufficient_stock(store):
    reserved = await store.soft_reserve("12345", 150, "res-2", ttl_seconds=60)
    assert reserved == 100  # only 100 available
    assert await store.check_stock("12345") == 0


@pytest.mark.asyncio
async def test_soft_reserve_unknown_sku(store):
    reserved = await store.soft_reserve("NOPE", 5, "res-3", ttl_seconds=60)
    assert reserved == 0


@pytest.mark.asyncio
async def test_confirm_reservation(store):
    await store.soft_reserve("ABC123", 10, "res-confirm", ttl_seconds=60)
    assert await store.confirm_reservation("res-confirm") is True
    # Confirmed reservation should not be released on expiry
    assert await store.check_stock("ABC123") == 490


@pytest.mark.asyncio
async def test_confirm_nonexistent_reservation(store):
    assert await store.confirm_reservation("does-not-exist") is False


@pytest.mark.asyncio
async def test_release_reservation(store):
    await store.soft_reserve("ABC123", 20, "res-release", ttl_seconds=60)
    assert await store.check_stock("ABC123") == 480
    assert await store.release_reservation("res-release") is True
    assert await store.check_stock("ABC123") == 500


@pytest.mark.asyncio
async def test_release_confirmed_reservation_no_restock(store):
    """Releasing a confirmed reservation should NOT return stock"""
    await store.soft_reserve("ABC123", 20, "res-conf-rel", ttl_seconds=60)
    await store.confirm_reservation("res-conf-rel")
    await store.release_reservation("res-conf-rel")
    # Stock should stay decremented since reservation was confirmed (purchased)
    assert await store.check_stock("ABC123") == 480


@pytest.mark.asyncio
async def test_ttl_expiry_releases_stock(store):
    """Stock should be released after TTL expires via run_cleanup"""
    await store.soft_reserve("ABC123", 50, "res-ttl", ttl_seconds=0)
    # TTL of 0 means already expired; run_cleanup releases stock
    await asyncio.sleep(0.01)
    cleaned, expired_ids = await store.run_cleanup()
    assert cleaned == 1
    assert len(expired_ids) == 1
    assert await store.check_stock("ABC123") == 500


@pytest.mark.asyncio
async def test_validate_all_accepted(store):
    orders = [Order(object="Widget", sku="ABC123", quantity=5)]
    response, status = await validate_and_reserve(orders, store, ttl_seconds=60)
    assert status == 200
    assert len(response.accepted_items) == 1
    assert len(response.rejected_items) == 0
    assert response.accepted_items[0].accepted is True


@pytest.mark.asyncio
async def test_validate_partial_fulfillment(store):
    orders = [
        Order(object="Widget", sku="ABC123", quantity=5),
        Order(object="Ghost", sku="NONEXISTENT", quantity=1),
    ]
    response, status = await validate_and_reserve(orders, store, ttl_seconds=60)
    assert status == 207
    assert len(response.accepted_items) == 1
    assert len(response.rejected_items) == 1


@pytest.mark.asyncio
async def test_validate_all_rejected(store):
    orders = [Order(object="Ghost", sku="NONEXISTENT", quantity=10)]
    response, status = await validate_and_reserve(orders, store, ttl_seconds=60)
    assert status == 409
    assert len(response.accepted_items) == 0
    assert len(response.rejected_items) == 1


@pytest.mark.asyncio
async def test_concurrent_reservations_no_oversell(store):
    """Multiple concurrent reservations should not exceed available stock"""

    async def reserve(i):
        return await store.soft_reserve(
            "12345", 60, f"concurrent-{i}", ttl_seconds=60
        )

    results = await asyncio.gather(reserve(1), reserve(2), reserve(3))
    total_reserved = sum(results)
    # Only 100 units available, so total reserved should not exceed 100
    assert total_reserved <= 100


# --- Store method tests: add_stock / set_stock ---


@pytest.mark.asyncio
async def test_add_stock_existing_sku(store):
    await store.add_stock("ABC123", 50)
    assert await store.check_stock("ABC123") == 550


@pytest.mark.asyncio
async def test_add_stock_new_sku(store):
    level = await store.add_stock("NEWSKU", 75)
    assert level == 75
    assert await store.check_stock("NEWSKU") == 75


@pytest.mark.asyncio
async def test_set_stock_existing_sku(store):
    level = await store.set_stock("ABC123", 999)
    assert level == 999
    assert await store.check_stock("ABC123") == 999


@pytest.mark.asyncio
async def test_set_stock_to_zero(store):
    level = await store.set_stock("ABC123", 0)
    assert level == 0
    assert await store.check_stock("ABC123") == 0


# --- Prefix-based confirm / release ---


@pytest.mark.asyncio
async def test_confirm_reservation_prefix(store):
    """Confirm by base UUID should confirm all sub-reservations"""
    base_id = "order-uuid-1"
    await store.soft_reserve("ABC123", 5, f"{base_id}:ABC123", ttl_seconds=60)
    await store.soft_reserve("DEF456", 3, f"{base_id}:DEF456", ttl_seconds=60)

    assert await store.confirm_reservation(base_id) is True
    # Both sub-reservations should be confirmed
    assert store._reservations[f"{base_id}:ABC123"]["confirmed"] is True
    assert store._reservations[f"{base_id}:DEF456"]["confirmed"] is True


@pytest.mark.asyncio
async def test_release_reservation_prefix(store):
    """Release by base UUID should release all sub-reservations and restore stock"""
    base_id = "order-uuid-2"
    await store.soft_reserve("ABC123", 10, f"{base_id}:ABC123", ttl_seconds=60)
    await store.soft_reserve("DEF456", 5, f"{base_id}:DEF456", ttl_seconds=60)

    assert await store.check_stock("ABC123") == 490
    assert await store.check_stock("DEF456") == 295

    assert await store.release_reservation(base_id) is True
    assert await store.check_stock("ABC123") == 500
    assert await store.check_stock("DEF456") == 300


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

    # Reset the global singletons to fresh instances for each test
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


def _place_order(client, items=None):
    """Helper to place an order and return the response JSON"""
    if items is None:
        items = [{"object": "Widget", "sku": "ABC123", "quantity": 2}]
    resp = client.post("/order-processing/order-intake", json=items)
    return resp.json()


def test_confirm_endpoint_success(client):
    order = _place_order(client)
    rid = order["reservation_id"]
    resp = client.post(
        "/order-processing/inventory/confirm",
        json={"reservation_id": rid},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"


def test_confirm_endpoint_not_found(client):
    resp = client.post(
        "/order-processing/inventory/confirm",
        json={"reservation_id": "bogus-id"},
    )
    assert resp.status_code == 404
    assert resp.json()["status"] == "not_found"


def test_release_endpoint_success(client):
    # Check stock before
    before = client.get("/order-processing/inventory/ABC123").json()["quantity"]
    order = _place_order(client)
    rid = order["reservation_id"]
    resp = client.post(
        "/order-processing/inventory/release",
        json={"reservation_id": rid},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "released"
    # Stock should be restored
    after = client.get("/order-processing/inventory/ABC123").json()["quantity"]
    assert after == before


def test_release_endpoint_not_found(client):
    resp = client.post(
        "/order-processing/inventory/release",
        json={"reservation_id": "bogus-id"},
    )
    assert resp.status_code == 404
    assert resp.json()["status"] == "not_found"


def test_restock_endpoint(client):
    before = client.get("/order-processing/inventory/ABC123").json()["quantity"]
    resp = client.post(
        "/order-processing/inventory/restock",
        json={"sku": "ABC123", "quantity": 50},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["previous_quantity"] == before
    assert data["current_quantity"] == before + 50


def test_get_single_sku(client):
    resp = client.get("/order-processing/inventory/ABC123")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sku"] == "ABC123"
    assert data["quantity"] > 0


def test_get_single_sku_unknown(client):
    resp = client.get("/order-processing/inventory/UNKNOWN")
    assert resp.status_code == 200
    assert resp.json()["quantity"] == 0
