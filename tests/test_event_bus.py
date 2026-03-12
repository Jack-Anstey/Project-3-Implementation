import asyncio
from dataclasses import dataclass
import pytest
from src.scripts.event_bus import EventBus


@dataclass(frozen=True)
class _FakeEvent:
    value: str


@dataclass(frozen=True)
class _OtherEvent:
    value: int


@pytest.fixture
def bus():
    return EventBus()


@pytest.mark.asyncio
async def test_subscribe_and_publish_triggers_handler(bus):
    results = []

    async def handler(event):
        results.append(event.value)

    bus.subscribe("_FakeEvent", handler)
    await bus.publish_and_wait(_FakeEvent(value="hello"))
    assert results == ["hello"]


@pytest.mark.asyncio
async def test_multiple_subscribers_all_fire(bus):
    results = []

    async def handler_a(event):
        results.append("a")

    async def handler_b(event):
        results.append("b")

    bus.subscribe("_FakeEvent", handler_a)
    bus.subscribe("_FakeEvent", handler_b)
    await bus.publish_and_wait(_FakeEvent(value="x"))
    assert results == ["a", "b"]


@pytest.mark.asyncio
async def test_publish_and_wait_completes_before_returning(bus):
    results = []

    async def slow_handler(event):
        await asyncio.sleep(0.05)
        results.append("done")

    bus.subscribe("_FakeEvent", slow_handler)
    await bus.publish_and_wait(_FakeEvent(value="x"))
    assert results == ["done"]


@pytest.mark.asyncio
async def test_publish_and_wait_propagates_errors(bus):
    async def bad_handler(event):
        raise ValueError("boom")

    bus.subscribe("_FakeEvent", bad_handler)
    with pytest.raises(ValueError, match="boom"):
        await bus.publish_and_wait(_FakeEvent(value="x"))


@pytest.mark.asyncio
async def test_fire_and_forget_publish_logs_errors(bus):
    results = []

    async def bad_handler(event):
        raise ValueError("boom")

    async def good_handler(event):
        results.append("ok")

    bus.subscribe("_FakeEvent", bad_handler)
    bus.subscribe("_FakeEvent", good_handler)
    await bus.publish(_FakeEvent(value="x"))
    # Allow tasks to complete
    await asyncio.sleep(0.1)
    assert results == ["ok"]


@pytest.mark.asyncio
async def test_no_subscribers_is_noop(bus):
    # Should not raise
    await bus.publish_and_wait(_FakeEvent(value="x"))
    await bus.publish(_FakeEvent(value="x"))


@pytest.mark.asyncio
async def test_different_event_types_isolated(bus):
    results = []

    async def fake_handler(event):
        results.append("fake")

    async def other_handler(event):
        results.append("other")

    bus.subscribe("_FakeEvent", fake_handler)
    bus.subscribe("_OtherEvent", other_handler)

    await bus.publish_and_wait(_FakeEvent(value="x"))
    assert results == ["fake"]

    await bus.publish_and_wait(_OtherEvent(value=1))
    assert results == ["fake", "other"]
