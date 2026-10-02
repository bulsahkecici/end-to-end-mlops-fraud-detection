from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.types import Message, Scope

from src.api.metrics import (
    AUTHENTICATION_FAILURES_TOTAL,
    REQUEST_BODY_REJECTIONS_TOTAL,
    REQUEST_COUNT,
)
from src.api.middleware import RequestBodyLimitMiddleware, register_middleware, setup_cors
from src.config import settings


def _exercise_limiter(
    chunks: list[bytes],
    *,
    limit: int,
    content_length: int | None,
) -> tuple[int, dict[str, Any], int, bool]:
    incoming: deque[Message] = deque(
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    )
    receive_calls = 0
    downstream_called = False
    sent: list[Message] = []

    headers = [(b"content-type", b"application/json")]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/predict",
        "raw_path": b"/predict",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("testclient", 123),
        "server": ("testserver", 80),
    }

    async def receive() -> Message:
        nonlocal receive_calls
        receive_calls += 1
        if incoming:
            return incoming.popleft()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    async def downstream(scope: Scope, receive, send) -> None:
        nonlocal downstream_called
        downstream_called = True
        payload = await Request(scope, receive).json()
        await JSONResponse({"parsed": payload})(scope, receive, send)

    middleware = RequestBodyLimitMiddleware(downstream, max_bytes=limit)
    asyncio.run(middleware(scope, receive, send))

    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return status, json.loads(response_body), receive_calls, downstream_called


def test_body_exactly_at_limit_succeeds():
    body = b'{"records":[{"TransactionAmt":1.0}]}'

    status, response, receive_calls, downstream_called = _exercise_limiter(
        [body], limit=len(body), content_length=len(body)
    )

    assert status == 200
    assert response == {"parsed": {"records": [{"TransactionAmt": 1.0}]}}
    assert receive_calls == 1
    assert downstream_called


def test_declared_body_above_limit_returns_413_without_reading_body():
    before = REQUEST_BODY_REJECTIONS_TOTAL._value.get()
    status, response, receive_calls, downstream_called = _exercise_limiter(
        [b'{"too":"large"}'], limit=5, content_length=15
    )

    assert status == 413
    assert response == {"detail": "Request body too large"}
    assert receive_calls == 0
    assert not downstream_called
    assert REQUEST_BODY_REJECTIONS_TOTAL._value.get() == before + 1


def test_missing_content_length_stops_receiving_when_body_exceeds_limit():
    status, response, receive_calls, downstream_called = _exercise_limiter(
        [b"123", b"456", b"must-not-be-read"], limit=5, content_length=None
    )

    assert status == 413
    assert response == {"detail": "Request body too large"}
    assert receive_calls == 2
    assert not downstream_called


def test_misleading_content_length_below_real_body_size_returns_413():
    status, response, receive_calls, downstream_called = _exercise_limiter(
        [b"123", b"456", b"must-not-be-read"], limit=5, content_length=2
    )

    assert status == 413
    assert response == {"detail": "Request body too large"}
    assert receive_calls == 2
    assert not downstream_called


def test_valid_chunked_body_is_replayed_once_for_downstream_json_parsing():
    body = b'{"records":[{"TransactionAmt":42.0}]}'

    status, response, receive_calls, downstream_called = _exercise_limiter(
        [body[:8], body[8:21], body[21:]], limit=len(body), content_length=None
    )

    assert status == 200
    assert response == {"parsed": {"records": [{"TransactionAmt": 42.0}]}}
    assert receive_calls == 3
    assert downstream_called


def _secured_app() -> FastAPI:
    app = FastAPI()
    setup_cors(app)
    register_middleware(app)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        return {"status": "ready"}

    @app.get("/metrics")
    def metrics():
        return {"status": "ok"}

    @app.post("/predict")
    def predict():
        return {"prediction": 0}

    @app.get("/items/{item_id}")
    def item(item_id: str):
        return {"item_id": item_id}

    return app


def test_api_key_protects_only_intended_routes_with_constant_time_comparison(monkeypatch):
    monkeypatch.setattr(settings, "api_key_enabled", True)
    monkeypatch.setattr(settings, "api_key", "correct-secret")
    comparisons: list[tuple[str, str]] = []

    def compare_digest(supplied: str, expected: str) -> bool:
        comparisons.append((supplied, expected))
        return supplied == expected

    monkeypatch.setattr("src.api.middleware.secrets.compare_digest", compare_digest)
    client = TestClient(_secured_app())
    before = AUTHENTICATION_FAILURES_TOTAL._value.get()

    for path in ("/health", "/ready", "/metrics"):
        assert client.get(path).status_code == 200
    assert client.post("/predict").status_code == 401
    assert client.post("/predict", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/predict", headers={"X-API-Key": "correct-secret"}).status_code == 200

    assert AUTHENTICATION_FAILURES_TOTAL._value.get() == before + 2
    assert ("correct-secret", "correct-secret") in comparisons


def test_cors_preflight_reaches_cors_middleware_before_auth(monkeypatch):
    monkeypatch.setattr(settings, "api_key_enabled", True)
    monkeypatch.setattr(settings, "api_key", "correct-secret")
    monkeypatch.setattr(settings, "cors_allow_origins", "https://frontend.example")
    client = TestClient(_secured_app())

    response = client.options(
        "/predict",
        headers={
            "Origin": "https://frontend.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-API-Key,Content-Type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://frontend.example"
    assert (
        client.post("/predict", headers={"Origin": "https://frontend.example"}).status_code == 401
    )
    assert (
        client.post(
            "/predict",
            headers={"Origin": "https://frontend.example", "X-API-Key": "correct-secret"},
        ).status_code
        == 200
    )
    assert (
        client.options("/predict", headers={"Origin": "https://frontend.example"}).status_code
        == 401
    )


def test_request_metrics_use_route_templates_and_collapse_unknown_paths(monkeypatch):
    monkeypatch.setattr(settings, "api_key_enabled", True)
    monkeypatch.setattr(settings, "api_key", "correct-secret")
    client = TestClient(_secured_app())
    template_counter = REQUEST_COUNT.labels(
        endpoint="/items/{item_id}", method="GET", status_code="200"
    )
    unmatched_counter = REQUEST_COUNT.labels(endpoint="unmatched", method="GET", status_code="404")
    template_before = template_counter._value.get()
    unmatched_before = unmatched_counter._value.get()

    assert (
        client.get("/items/arbitrary-value", headers={"X-API-Key": "correct-secret"}).status_code
        == 200
    )
    unknown_paths = [f"/unknown/random-path-{index}/value-{index * 17}" for index in range(25)]
    for path in unknown_paths:
        assert client.get(path, headers={"X-API-Key": "correct-secret"}).status_code == 404

    assert template_counter._value.get() == template_before + 1
    assert unmatched_counter._value.get() == unmatched_before + len(unknown_paths)
    endpoint_labels = {
        sample.labels.get("endpoint")
        for metric in REQUEST_COUNT.collect()
        for sample in metric.samples
        if sample.name == "http_requests_total"
    }
    assert not endpoint_labels.intersection(unknown_paths)


def test_request_metrics_collapse_nonstandard_methods(monkeypatch):
    monkeypatch.setattr(settings, "api_key_enabled", False)
    client = TestClient(_secured_app())
    counter = REQUEST_COUNT.labels(endpoint="/predict", method="OTHER", status_code="405")
    before = counter._value.get()

    assert client.request("USER-CONTROLLED-METHOD", "/predict").status_code == 405

    assert counter._value.get() == before + 1
