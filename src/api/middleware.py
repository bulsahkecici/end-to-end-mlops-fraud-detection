"""Cross-cutting HTTP middleware: request correlation id, structured access
logging, request-size limiting, optional API-key auth, and Prometheus
request metrics.

Kept deliberately dependency-free (no external ASGI middleware packages) so
the security behaviour is easy to audit in one place.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.api.metrics import REQUEST_COUNT, REQUEST_LATENCY_SECONDS
from src.config import settings

logger = logging.getLogger("src.api")

_UNAUTHENTICATED_PATHS = {"/health", "/ready", "/metrics"}


class RequestBodyLimitMiddleware:
    """Enforce a byte limit without buffering an oversized request body.

    Valid request messages are replayed exactly once to downstream Starlette
    consumers.  At most ``max_bytes`` of body data is retained; the receive
    loop stops as soon as a chunk would cross the configured limit.
    """

    def __init__(self, app: ASGIApp, max_bytes: int | None = None) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.max_bytes if self.max_bytes is not None else settings.api_max_request_bytes
        if _declared_body_exceeds_limit(scope, limit):
            await _request_too_large_response(scope, receive, send)
            return

        buffered_messages: deque[Message] = deque()
        received_bytes = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                buffered_messages.append(message)
                break

            body = message.get("body", b"")
            received_bytes += len(body)
            if received_bytes > limit:
                await _request_too_large_response(scope, receive, send)
                return

            buffered_messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            if buffered_messages:
                return buffered_messages.popleft()
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


def _declared_body_exceeds_limit(scope: Scope, limit: int) -> bool:
    for name, value in scope.get("headers", []):
        if name.lower() != b"content-length":
            continue
        try:
            if int(value) > limit:
                return True
        except ValueError:
            continue
    return False


async def _request_too_large_response(scope: Scope, receive: Receive, send: Send) -> None:
    response = JSONResponse(status_code=413, content={"detail": "Request body too large"})
    await response(scope, receive, send)


def setup_cors(app: FastAPI) -> None:
    origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def register_middleware(app: FastAPI) -> None:
    # Register before the decorator below so request-context logging remains
    # outside the limiter and records early 413 responses as well.
    app.add_middleware(RequestBodyLimitMiddleware)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = str(uuid.uuid4())
        start = time.perf_counter()
        endpoint = request.url.path

        needs_auth = settings.api_key_enabled and endpoint not in _UNAUTHENTICATED_PATHS
        if needs_auth and request.headers.get("X-API-Key") != settings.api_key:
            response = JSONResponse(
                status_code=401, content={"detail": "Invalid or missing API key"}
            )
            logger.warning("unauthorized", extra={"request_id": request_id, "endpoint": endpoint})
        else:
            try:
                response = await call_next(request)
            except Exception:  # noqa: BLE001 - never leak a raw traceback to the client
                logger.exception(
                    "unhandled_exception",
                    extra={"request_id": request_id, "endpoint": endpoint},
                )
                response = JSONResponse(
                    status_code=500, content={"detail": "Internal server error"}
                )

        latency_seconds = time.perf_counter() - start
        response.headers["X-Request-ID"] = request_id
        REQUEST_COUNT.labels(
            endpoint=endpoint, method=request.method, status_code=str(response.status_code)
        ).inc()
        REQUEST_LATENCY_SECONDS.labels(endpoint=endpoint).observe(latency_seconds)
        logger.info(
            "request_handled",
            extra={
                "request_id": request_id,
                "endpoint": endpoint,
                "status_code": response.status_code,
                "latency_ms": round(latency_seconds * 1000, 2),
            },
        )
        return response
