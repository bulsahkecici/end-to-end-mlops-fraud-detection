from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import Message, Scope

from src.api.middleware import RequestBodyLimitMiddleware


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
    status, response, receive_calls, downstream_called = _exercise_limiter(
        [b'{"too":"large"}'], limit=5, content_length=15
    )

    assert status == 413
    assert response == {"detail": "Request body too large"}
    assert receive_calls == 0
    assert not downstream_called


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
