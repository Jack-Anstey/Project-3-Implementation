import asyncio
from datetime import datetime, timezone
import pytest
from src.scripts.retry_queue import InMemoryRetryQueue


@pytest.fixture
def queue():
    return InMemoryRetryQueue()


@pytest.mark.asyncio
async def test_enqueue_stores_task(queue):
    queue.register_handler("noop", lambda: None)
    task_id = await queue.enqueue("noop")
    pending = await queue.get_pending()
    assert len(pending) == 1
    assert pending[0]["task_id"] == task_id


@pytest.mark.asyncio
async def test_process_pending_success(queue):
    calls = []

    async def handler(x):
        calls.append(x)

    queue.register_handler("test_handler", handler)
    await queue.enqueue("test_handler", "hello")
    processed = await queue.process_pending()
    assert processed == 1
    assert calls == ["hello"]
    assert await queue.get_pending() == []


@pytest.mark.asyncio
async def test_process_pending_retry_on_failure(queue):
    async def failing_handler():
        raise RuntimeError("boom")

    queue.register_handler("fail", failing_handler)
    await queue.enqueue("fail", max_retries=3)
    await queue.process_pending()
    pending = await queue.get_pending()
    assert len(pending) == 1
    assert pending[0]["attempts"] == 1
    assert pending[0]["last_error"] == "boom"


@pytest.mark.asyncio
async def test_dead_letter_after_max_retries(queue):
    async def always_fail():
        raise RuntimeError("permanent failure")

    queue.register_handler("fail", always_fail)
    await queue.enqueue("fail", max_retries=2)

    # First attempt
    await queue.process_pending()
    assert len(await queue.get_pending()) == 1

    # Force next_retry_at to now so it gets picked up
    queue._pending[0]["next_retry_at"] = datetime.now(timezone.utc).isoformat()
    await queue.process_pending()

    # After 2 attempts (== max_retries), should be dead-lettered
    assert len(await queue.get_pending()) == 0
    dead = await queue.get_dead_letters()
    assert len(dead) == 1
    assert dead[0]["callable_name"] == "fail"


@pytest.mark.asyncio
async def test_exponential_backoff(queue):
    async def failing():
        raise RuntimeError("fail")

    queue.register_handler("fail", failing)
    await queue.enqueue("fail", max_retries=5)
    await queue.process_pending()
    pending = await queue.get_pending()
    assert len(pending) == 1
    next_retry = datetime.fromisoformat(pending[0]["next_retry_at"])
    assert next_retry > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_queue_cap_evicts_oldest():
    queue = InMemoryRetryQueue(max_queue_size=3)
    queue.register_handler("noop", lambda: None)
    ids = []
    for _ in range(5):
        ids.append(await queue.enqueue("noop"))
    pending = await queue.get_pending()
    assert len(pending) == 3
    # Oldest two should be evicted
    pending_ids = [t["task_id"] for t in pending]
    assert ids[0] not in pending_ids
    assert ids[1] not in pending_ids
    assert ids[4] in pending_ids


@pytest.mark.asyncio
async def test_dead_letter_cap():
    queue = InMemoryRetryQueue(max_dead_letters=2)

    async def always_fail():
        raise RuntimeError("fail")

    queue.register_handler("fail", always_fail)
    # Enqueue 3 tasks that will all fail immediately
    for _ in range(3):
        await queue.enqueue("fail", max_retries=1)

    await queue.process_pending()
    dead = await queue.get_dead_letters()
    assert len(dead) == 2


@pytest.mark.asyncio
async def test_concurrent_enqueue(queue):
    queue.register_handler("noop", lambda: None)

    async def enqueue_one(i):
        return await queue.enqueue("noop")

    results = await asyncio.gather(*[enqueue_one(i) for i in range(20)])
    assert len(results) == 20
    pending = await queue.get_pending()
    assert len(pending) == 20
