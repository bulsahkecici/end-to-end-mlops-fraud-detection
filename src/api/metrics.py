"""Prometheus metrics for the fraud-detection API.

``model_version`` is only ever used as a label on the single low-cardinality
``model_info`` gauge (one active version per process at a time) — never on
high-frequency counters/histograms like request counts or latency, where a
label per model version would grow the series count unboundedly across
redeployments.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests handled",
    ["endpoint", "method", "status_code"],
)
REQUEST_LATENCY_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["endpoint"],
)
PREDICT_BATCH_SIZE = Histogram(
    "predict_batch_size",
    "Number of records in a single /predict request",
    buckets=(1, 2, 5, 10, 25, 50, 100, 250, 500),
)
PREDICTIONS_TOTAL = Counter("predictions_total", "Total individual predictions returned")
FRAUD_PREDICTIONS_TOTAL = Counter(
    "fraud_predictions_total", "Total predictions where fraud_prediction == 1"
)
PREDICTION_EXCEPTIONS_TOTAL = Counter(
    "prediction_exceptions_total", "Total exceptions raised while handling /predict"
)
MODEL_LOADED = Gauge("model_loaded", "1 if a model is currently loaded and ready, else 0")
MODEL_INFO = Gauge(
    "model_info",
    "Static info about the currently loaded model version (value is always 1)",
    ["model_name", "model_version"],
)


def render_latest() -> bytes:
    return generate_latest()


__all__ = [
    "CONTENT_TYPE_LATEST",
    "REQUEST_COUNT",
    "REQUEST_LATENCY_SECONDS",
    "PREDICT_BATCH_SIZE",
    "PREDICTIONS_TOTAL",
    "FRAUD_PREDICTIONS_TOTAL",
    "PREDICTION_EXCEPTIONS_TOTAL",
    "MODEL_LOADED",
    "MODEL_INFO",
    "render_latest",
]
