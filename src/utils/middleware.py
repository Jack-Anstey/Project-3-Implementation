# Native Imports
import asyncio

# Third Party Imports
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class TimeoutMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that enforces a per-request timeout"""

    def __init__(self, app, timeout: int = 30) -> None:
        super().__init__(app)
        self.timeout = timeout

    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            return await asyncio.wait_for(call_next(request), timeout=self.timeout)
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=504,
                content={"detail": "Request timed out"},
            )
