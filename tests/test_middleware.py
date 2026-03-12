import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.utils.middleware import TimeoutMiddleware


def _create_app(timeout: int) -> FastAPI:
    app = FastAPI()
    app.add_middleware(TimeoutMiddleware, timeout=timeout)

    @app.get("/fast")
    async def fast():
        return {"status": "ok"}

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(10)
        return {"status": "done"}

    return app


def test_request_completes_within_timeout():
    app = _create_app(timeout=5)
    client = TestClient(app)
    resp = client.get("/fast")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_request_exceeds_timeout_returns_504():
    app = _create_app(timeout=1)
    client = TestClient(app)
    resp = client.get("/slow")
    assert resp.status_code == 504
    assert resp.json()["detail"] == "Request timed out"
