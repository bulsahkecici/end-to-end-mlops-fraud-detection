"""Structured (JSON) logging configuration shared by the API and CLI entrypoints.

Never log raw request payloads or transaction contents here — only
request/response metadata (ids, endpoint, status, latency, model info).
"""

from __future__ import annotations

import json
import logging
import sys

_EXTRA_FIELDS = (
    "request_id",
    "endpoint",
    "status_code",
    "latency_ms",
    "model_name",
    "model_version",
    "batch_size",
    "error_type",
    "deployment_id",
    "action",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in _EXTRA_FIELDS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
