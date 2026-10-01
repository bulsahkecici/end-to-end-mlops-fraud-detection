# Monitoring

## Structured logging

`src/logging_config.py` configures a JSON log formatter used by everything
under `src/api/`. Every request produces a `request_handled` line; rejected or
failed requests also produce a bounded event such as `unauthorized` or
`unhandled_exception`:

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

The endpoint is a configured route template, never the raw URL path; unknown
paths use the fixed value `unmatched`. `model_name`, `model_version`,
`deployment_id`, deployment `action`, `batch_size`, and exception class
`error_type` are attached where relevant. Raw exception messages and
tracebacks are not logged at the API boundary because third-party messages can
contain artifact paths or connection details. **Raw transaction payloads,
prediction values, API keys, credentials, and artifact contents are never
logged**. Every
response also carries an `X-Request-ID` header matching the log line, so a
client-reported issue can be traced to its exact log entry.

## Prometheus metrics (`GET /metrics`)

| Metric | Type | Labels | Notes |
|---|---|---|---|
| `http_requests_total` | Counter | `endpoint`, `method`, `status_code` | endpoint is a known route template or `unmatched` |
| `http_request_duration_seconds` | Histogram | `endpoint` | endpoint is a known route template or `unmatched` |
| `predict_batch_size` | Histogram | — | records per `/predict` call |
| `predictions_total` | Counter | — | individual predictions returned |
| `fraud_predictions_total` | Counter | — | predictions with `fraud_prediction == 1` |
| `prediction_exceptions_total` | Counter | — | exceptions during `/predict` |
| `authentication_failures_total` | Counter | — | API-key rejections |
| `request_body_size_rejections_total` | Counter | — | in-process body-limit rejections |
| `semantic_validation_failures_total` | Counter | `code` | rejected requests; one issue uses its allowlisted code, while multiple issues use `multiple` and unknown single codes use `other` |
| `readiness_failures_total` | Counter | — | `/ready` responses reporting unavailable |
| `model_load_failures_total` | Counter | — | failed startup model-load attempts |
| `model_loaded` | Gauge | — | 1 if ready, 0 otherwise |
| `model_info` | Gauge | `model_name`, `model_version` | always 1; a label carrier, not a real gauge |

`model_version` is deliberately **only** used as a label on `model_info` —
a single low-cardinality gauge with at most one active version at a time —
never on `http_requests_total`/`http_request_duration_seconds`, where a
label per redeployed model version would grow the series count without
bound over the service's lifetime.

No metric label contains a raw URL, request/record/run/deployment ID, error
text, API key, or request value. HTTP methods are limited to standard methods
plus `OTHER`; status codes are finite; semantic validation codes are explicitly
allowlisted.

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

Drift monitoring is an offline, reference-vs-current comparison with an
explicit operator-supplied contract. It never infers feature roles, numeric
bins, or category buckets from the current batch. The operator must copy
`feature_schema.numeric_cols` and `feature_schema.categorical_cols` from the
deployed model's persisted `metadata.json`; the command fails closed if any
contract feature is absent from the reference. The monitor does not load that
model metadata itself and therefore does not independently prove that the
operator supplied every deployed-model feature. Optional deployment state is
validated against the contract's immutable model name, model version, and run
ID before its deployment ID is added to the report; this validates identity,
not feature-schema completeness.

Minimal contract shape:

```json
{
  "contract_schema_version": "1.0",
  "model": {
    "model_name": "ieee-fraud-model",
    "model_version": "7",
    "run_id": "<immutable-run-id>",
    "deployment_id": null,
    "threshold": 0.42,
    "threshold_source": "model_metadata"
  },
  "feature_schema": {
    "numeric_cols": ["TransactionAmt", "TransactionDT"],
    "categorical_cols": ["ProductCD"]
  },
  "monitoring": {
    "numeric_bin_count": 10,
    "categorical_top_k": 10,
    "probability_column": "fraud_probability",
    "decision_column": "fraud_prediction"
  }
}
```

Prediction columns are optional. If they are not configured, prediction
monitoring is explicitly `NOT_EVALUATED`; `isFraud` or any other label column
is never relabeled as prediction drift.

```bash
python -m src.monitoring.drift \
  --reference reference.csv \
  --current current.csv \
  --contract monitoring-contract.json \
  --deployment-state artifacts/deployment/current.json \
  --reference-source warehouse.training-reference.v1 \
  --current-source inference-export.2026-02 \
  --reference-window-start 2026-01-01T00:00:00+00:00 \
  --reference-window-end 2026-01-31T23:59:59+00:00 \
  --current-window-start 2026-02-01T00:00:00+00:00 \
  --current-window-end 2026-02-28T23:59:59+00:00 \
  --output-json reports/drift-model-7-2026-02.json \
  --output-markdown reports/drift-model-7-2026-02.md
```

`make drift-report DRIFT_ARGS='...'` is a thin wrapper around the same command.
Missing arguments show usage and return nonzero. Outputs are explicit and are
created exclusively; an existing file is rejected unless `--overwrite` is
deliberately supplied. JSON and Markdown are fully rendered into sibling
temporary files before publication. If the second publication fails, the first
is removed or restored to its prior contents. This is a small best-effort local
filesystem transaction with file-content syncing and atomic renames/links; it
is not a distributed transaction or a guarantee against every storage or
process failure.

### Deterministic report contract

The report's `semantic` object contains model/run/deployment/threshold
provenance, source and UTC window metadata, row counts, fingerprints, feature
evidence, prediction evidence, bounded checks, and the stable overall result.
`generated_at` is outside `semantic`, and `semantic_identity_sha256` hashes only
the canonical semantic object. Repeating a run with equal inputs and config
therefore preserves semantic equality even though generation time changes.

Input fingerprints SHA-256 hash sorted column names plus the sorted multiset of
canonical row values. Dataframe index, row order, column order, in-memory
layout, and dtype storage details are excluded; duplicate-row multiplicity,
missing/non-finite markers, every column name, and every cell value are
included. Separate schema and config fingerprints hash their canonical
monitoring-contract content. These hashes are reproducibility evidence, not
cryptographic proof of external-storage integrity.

### Feature and prediction evidence

- **Numeric features:** missing, finite, and invalid rates; finite mean, standard
  deviation, min/max, and 5/50/95% quantiles; plus total-variation distance over
  quantile bins derived once from reference and applied unchanged to current.
  Constant references use fixed below/equal/above buckets. All-null/non-finite
  references remain explicit and distribution/standardized-mean checks become
  `NOT_EVALUATED`. Missing current columns are represented and breach the
  presence check. JSON serialization rejects NaN and Infinity.
- **Categorical features:** the reference's deterministic top-K plus `OTHER`,
  `MISSING`, and `UNKNOWN`. Category identifiers in reports are SHA-256 tokens,
  so raw high-cardinality values are not emitted. These deterministic unsalted
  tokens are bounded pseudonymous identifiers, not secrecy or anonymization;
  low-entropy values can still be guessed. Current-only values can only
  increase `UNKNOWN`; current data can never expand report cardinality.
- **Prediction output:** configured probability columns report fixed probability
  deciles, invalid rate, mean, and distribution shift. Configured decision
  columns report positive-decision rate. The model's stored threshold and its
  source are provenance only; monitoring never selects or changes a threshold.
  A configured reference prediction column with no valid values fails closed.
  If current data has no valid comparable values, its component is
  `NOT_EVALUATED`, its availability check is a `BREACH`, and undefined
  distribution/mean or positive-rate checks cannot report `PASS`.

Every check has `check_id`, `feature`, `metric`, `observed`, `threshold`,
`status`, and `severity`. Status is one of `PASS`, `WARN`, `BREACH`, or
`NOT_EVALUATED`. Exit 0 means no breach (PASS or WARN), exit 1 means at least one
BREACH, and exit 2 means invalid contract/input/output handling. WARN does not
fail the command.

## What isn't covered

- No automated alerting is wired up (Prometheus Alertmanager / PagerDuty
  integration would be the natural next step, using the metrics above).
- No prediction-log persistence for offline drift analysis — the drift
  command expects the caller to supply a "current" CSV (e.g. exported from
  wherever inference requests are archived); this project does not itself
  persist a rolling window of served requests.
- No delayed-label performance monitoring, label warehouse, streaming
  monitoring, automatic retraining/promotion/rollback, or alert delivery.
