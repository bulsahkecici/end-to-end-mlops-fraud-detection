# Monitoring

## Structured logging

`src/logging_config.py` configures a JSON log formatter used by everything
under `src/api/`. Every request produces one `request_handled` (or
`unauthorized` / `unhandled_exception`) log line with:

```json
{
  "timestamp": "2026-01-01T12:00:00+0000",
  "level": "INFO",
  "logger": "src.api",
  "message": "request_handled",
  "request_id": "…",
  "endpoint": "/predict",
  "status_code": 200,
  "latency_ms": 6.12
}
```

`model_name`, `model_version`, and `batch_size` are attached where
relevant (model load, prediction failures). **Raw transaction payloads and
prediction values are never logged** — only request metadata. Every
response also carries an `X-Request-ID` header matching the log line, so a
client-reported issue can be traced to its exact log entry.

## Prometheus metrics (`GET /metrics`)

| Metric | Type | Labels | Notes |
|---|---|---|---|
| `http_requests_total` | Counter | `endpoint`, `method`, `status_code` | |
| `http_request_duration_seconds` | Histogram | `endpoint` | |
| `predict_batch_size` | Histogram | — | records per `/predict` call |
| `predictions_total` | Counter | — | individual predictions returned |
| `fraud_predictions_total` | Counter | — | predictions with `fraud_prediction == 1` |
| `prediction_exceptions_total` | Counter | — | exceptions during `/predict` |
| `model_loaded` | Gauge | — | 1 if ready, 0 otherwise |
| `model_info` | Gauge | `model_name`, `model_version` | always 1; a label carrier, not a real gauge |

`model_version` is deliberately **only** used as a label on `model_info` —
a single low-cardinality gauge with at most one active version at a time —
never on `http_requests_total`/`http_request_duration_seconds`, where a
label per redeployed model version would grow the series count without
bound over the service's lifetime.

Example scrape config:

```yaml
scrape_configs:
  - job_name: ieee-fraud-api
    static_configs:
      - targets: ["api:8000"]
    metrics_path: /metrics
```

Useful PromQL starting points:

```promql
sum(rate(http_requests_total{status_code!~"2.."}[5m]))        # error rate
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))  # p95 latency
rate(fraud_predictions_total[1h]) / rate(predictions_total[1h])           # rolling fraud rate
```

## Drift monitoring

```bash
python -m src.monitoring.drift --synthetic                              # self-contained demo
python -m src.monitoring.drift --reference train.csv --current batch.csv
make drift-report                                                       # same as --synthetic
```

Compares a reference dataset (typically training data) against a current
batch column by column:

- **Numeric columns**: standardized mean shift (`|current_mean -
  reference_mean| / reference_std`) and missing-rate shift. Flagged if the
  standardized shift exceeds `NUMERIC_MEAN_SHIFT_THRESHOLD` (0.5) or the
  missing-rate shift exceeds `MISSING_RATE_SHIFT_THRESHOLD` (0.10).
- **Categorical columns**: max absolute change in category frequency
  share, plus any brand-new categories. Flagged above
  `CATEGORY_SHIFT_THRESHOLD` (0.10).
- **Prediction/label drift**: fraud-rate shift, when a label column is
  present on both sides.

Writes `reports/drift_report.json` and `reports/drift_report.md`, and
prints the JSON report to stdout. This is intentionally a lightweight,
dependency-free check (plain pandas/numpy summary statistics) rather than
a full framework like Evidently — appropriate for this project's size; a
heavier framework would be a reasonable upgrade if drift monitoring needs
to become more central to operations (e.g. PSI-based scoring, automated
alerting integration).

## What isn't covered

- No automated alerting is wired up (Prometheus Alertmanager / PagerDuty
  integration would be the natural next step, using the metrics above).
- No prediction-log persistence for offline drift analysis — the drift
  command expects the caller to supply a "current" CSV (e.g. exported from
  wherever inference requests are archived); this project does not itself
  persist a rolling window of served requests.
