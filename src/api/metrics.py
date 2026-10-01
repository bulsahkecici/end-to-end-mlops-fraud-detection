"""Prometheus metrics for the fraud-detection API.

``model_version`` is only ever used as a label on the single low-cardinality
``model_info`` gauge (one active version per process at a time) — never on
high-frequency counters/histograms like request counts or latency, where a
label per model version would grow the series count unboundedly across
redeployments.
"""

from __future__ import annotations

from collections.abc import Iterable

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
AUTHENTICATION_FAILURES_TOTAL = Counter(
    "authentication_failures_total", "Total requests rejected by API-key authentication"
)
REQUEST_BODY_REJECTIONS_TOTAL = Counter(
    "request_body_size_rejections_total", "Total requests rejected for exceeding the body limit"
)
SEMANTIC_VALIDATION_FAILURES_TOTAL = Counter(
    "semantic_validation_failures_total",
    "Total prediction requests rejected by semantic validation",
    ["code"],
)
READINESS_FAILURES_TOTAL = Counter(
    "readiness_failures_total", "Total readiness checks that reported the service unavailable"
)
MODEL_LOAD_FAILURES_TOTAL = Counter(
    "model_load_failures_total", "Total model load attempts that failed"
)
MODEL_LOADED = Gauge("model_loaded", "1 if a model is currently loaded and ready, else 0")
MODEL_INFO = Gauge(
    "model_info",
    "Static info about the currently loaded model version (value is always 1)",
    ["model_name", "model_version"],
)

_SEMANTIC_VALIDATION_CODES = frozenset(
    {
        "invalid_categorical",
        "invalid_numeric",
        "no_usable_features",
        "non_scalar_feature",
    }
)


def semantic_validation_code_label(code: str) -> str:
    """Bound semantic-validation metric labels even if a new code is introduced."""
    return code if code in _SEMANTIC_VALIDATION_CODES else "other"


def semantic_validation_reason_label(codes: Iterable[str]) -> str:
    """Return one bounded label for one rejected semantic-validation request."""
    labels = [semantic_validation_code_label(code) for code in codes]
    if len(labels) != 1:
        return "multiple"
    return labels[0]


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
    "AUTHENTICATION_FAILURES_TOTAL",
    "REQUEST_BODY_REJECTIONS_TOTAL",
    "SEMANTIC_VALIDATION_FAILURES_TOTAL",
    "READINESS_FAILURES_TOTAL",
    "MODEL_LOAD_FAILURES_TOTAL",
    "MODEL_LOADED",
    "MODEL_INFO",
    "semantic_validation_code_label",
    "semantic_validation_reason_label",
    "render_latest",
]
