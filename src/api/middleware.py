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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.metrics import REQUEST_COUNT, REQUEST_LATENCY_SECONDS
from src.config import settings

logger = logging.getLogger("src.api")

_UNAUTHENTICATED_PATHS = {"/health", "/ready", "/metrics"}


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
            content_length = request.headers.get("content-length")
            too_large = False
            if content_length is not None:
                try:
                    too_large = int(content_length) > settings.api_max_request_bytes
                except ValueError:
                    too_large = False

            # Content-Length is optional and cannot be trusted on its own.
            # Starlette caches request.body(), so downstream parsing sees the
            # same bytes without a second network read.
            if not too_large:
                body = await request.body()
                too_large = len(body) > settings.api_max_request_bytes

            if too_large:
                response = JSONResponse(
                    status_code=413, content={"detail": "Request body too large"}
                )
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
