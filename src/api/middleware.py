"""Cross-cutting HTTP middleware: request correlation id, structured access
logging, request-size limiting, optional API-key auth, and Prometheus
request metrics.

Kept deliberately dependency-free (no external ASGI middleware packages) so
the security behaviour is easy to audit in one place.
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from collections import deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.api.metrics import (
    AUTHENTICATION_FAILURES_TOTAL,
    REQUEST_BODY_REJECTIONS_TOTAL,
    REQUEST_COUNT,
    REQUEST_LATENCY_SECONDS,
)
from src.config import settings

logger = logging.getLogger("src.api")

_UNAUTHENTICATED_PATHS = {"/health", "/ready", "/metrics"}
_UNMATCHED_ROUTE_LABEL = "unmatched"
_KNOWN_HTTP_METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})


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
    REQUEST_BODY_REJECTIONS_TOTAL.inc()
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
        endpoint = normalized_route_label(request)
        method = normalized_method_label(request.method)

        needs_auth = (
            settings.api_key_enabled
            and endpoint not in _UNAUTHENTICATED_PATHS
            and not _is_cors_preflight(request)
        )
        supplied_key = request.headers.get("X-API-Key", "")
        expected_key = settings.api_key or ""
        key_is_valid = bool(expected_key) and secrets.compare_digest(supplied_key, expected_key)
        if needs_auth and not key_is_valid:
            AUTHENTICATION_FAILURES_TOTAL.inc()
            response = JSONResponse(
                status_code=401, content={"detail": "Invalid or missing API key"}
            )
            logger.warning("unauthorized", extra={"request_id": request_id, "endpoint": endpoint})
        else:
            try:
                response = await call_next(request)
            except Exception as exc:  # noqa: BLE001 - keep public failure generic
                logger.error(
                    "unhandled_exception",
                    extra={
                        "request_id": request_id,
                        "endpoint": endpoint,
                        "error_type": type(exc).__name__,
                    },
                )
                response = JSONResponse(
                    status_code=500, content={"detail": "Internal server error"}
                )

        latency_seconds = time.perf_counter() - start
        response.headers["X-Request-ID"] = request_id
        REQUEST_COUNT.labels(
            endpoint=endpoint, method=method, status_code=str(response.status_code)
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


def normalized_route_label(request: Request) -> str:
    """Return a configured route template or one fixed label for unknown paths."""
    for route in request.app.routes:
        match, _ = route.matches(request.scope)
        if match is not Match.NONE:
            path = getattr(route, "path", None)
            if isinstance(path, str):
                return path
    return _UNMATCHED_ROUTE_LABEL


def normalized_method_label(method: str) -> str:
    normalized = method.upper()
    return normalized if normalized in _KNOWN_HTTP_METHODS else "OTHER"


def _is_cors_preflight(request: Request) -> bool:
    return (
        request.method == "OPTIONS"
        and "origin" in request.headers
        and "access-control-request-method" in request.headers
    )
