"""Cross-cutting HTTP middleware: request correlation id, structured access
logging, request-size limiting and optional API-key auth.

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

        if settings.api_key_enabled and request.url.path not in _UNAUTHENTICATED_PATHS:
            if request.headers.get("X-API-Key") != settings.api_key:
                latency_ms = (time.perf_counter() - start) * 1000
                logger.warning(
                    "unauthorized",
                    extra={
                        "request_id": request_id,
                        "endpoint": request.url.path,
                        "status_code": 401,
                        "latency_ms": round(latency_ms, 2),
                    },
                )
                return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > settings.api_max_request_bytes:
                    return JSONResponse(status_code=413, content={"detail": "Request body too large"})
            except ValueError:
                pass

        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 - last line of defense: never leak a raw traceback
            logger.exception(
                "unhandled_exception", extra={"request_id": request_id, "endpoint": request.url.path}
            )
            response = JSONResponse(status_code=500, content={"detail": "Internal server error"})

        latency_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_handled",
            extra={
                "request_id": request_id,
                "endpoint": request.url.path,
                "status_code": response.status_code,
                "latency_ms": round(latency_ms, 2),
            },
        )
        return response
