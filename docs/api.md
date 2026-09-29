# API reference

Base URL: `http://localhost:8000` (default `make serve` / `docker-compose` port).

## `GET /health`

Liveness only — returns `200` once the process is up, regardless of model
state.

```bash
curl -s http://localhost:8000/health
# {"status":"ok"}
```

## `GET /ready`

Readiness — `200` only if a model is loaded from the MLflow registry.
`503` with an explanatory `detail` otherwise (e.g. no `champion` alias set
yet — run `make promote` first).

```bash
curl -s http://localhost:8000/ready
# {"status":"ready","model_name":"ieee_fraud_lgbm","model_version":"1","model_source":"alias:champion"}
```

## `POST /predict`

Request body:

```json
{
  "records": [
    { "TransactionAmt": 100.0 }
  ]
}
```

- `records` is a non-empty list of 1 to `API_MAX_BATCH_SIZE` (default 500)
  JSON objects. Unexpected top-level request fields are rejected.
- Every model feature is optional, but every record must contain at least one
  recognized model feature with a non-missing value. Empty, extra-only, and
  all-null records are rejected as semantically unusable.
- Model feature names and numeric/categorical roles come from the loaded
  model artifact's fitted schema metadata; the API does not maintain a
  hardcoded copy of the training schema.
- Numeric features accept finite JSON numbers and finite numeric strings.
  Boolean, blank, non-numeric, `NaN`, and infinite values are rejected.
- Categorical features accept nonblank strings. Categories never seen during
  training remain supported and map to the fitted encoder's unknown code.
- `null` represents an optional missing value and is handled by the fitted
  training-time imputer. It does not count as a usable value by itself.
- Objects and arrays are rejected when supplied for a recognized scalar model
  feature. Extra record fields are ignored (including their values) and do
  not count toward semantic usability. Column order remains irrelevant.
- Validation never transforms an accepted request. Accepted records flow
  unchanged through the serialized fitted aligner, preprocessor, and model.

Response:

```json
{
  "predictions": [
    { "fraud_probability": 0.051, "fraud_prediction": 0, "threshold": 0.057 }
  ],
  "model_name": "ieee_fraud_lgbm",
  "model_version": "1"
}
```

`threshold` is the decision threshold selected on the validation set at
training time — it is echoed per-prediction so a caller never has to guess
what cutoff produced `fraud_prediction`.

### Examples

Single record (matches the README quickstart):

```bash
curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"records": [{"TransactionAmt": 100.0}]}'
```

Batch:

```bash
curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"records": [{"TransactionAmt": 1.0}, {"TransactionAmt": 500.0, "ProductCD": "C"}]}'
```

A fuller record:

```bash
curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"records": [{
        "TransactionAmt": 249.0,
        "ProductCD": "W",
        "card4": "visa",
        "card6": "credit",
        "P_emaildomain": "gmail.com",
        "M1": "T"
      }]}'
```

### Error responses

| Condition | Status | Body |
|---|---|---|
| Empty `records` list | `422` | pydantic validation error |
| Malformed JSON | `422` | pydantic/FastAPI validation error |
| More than `API_MAX_BATCH_SIZE` records | `422` | pydantic validation error |
| Empty, extra-only, or all-null record | `422` | structured `semantic_validation_failed` detail |
| Malformed or non-scalar recognized model feature | `422` | structured error with record index, field, and code |
| Request body larger than `API_MAX_REQUEST_BYTES` | `413` | `{"detail": "Request body too large"}` |
| No model loaded | `503` | `{"detail": "Model not loaded. ..."}` |
| Missing/invalid `X-API-Key` (only when `API_KEY_ENABLED=true`) | `401` | `{"detail": "Invalid or missing API key"}` |
| Unexpected internal error | `500` | generic `{"detail": ...}` — never a raw traceback |

Semantic validation errors use this stable shape and never echo rejected
values:

```json
{
  "detail": {
    "code": "semantic_validation_failed",
    "message": "One or more records failed semantic validation.",
    "errors": [
      {
        "record_index": 0,
        "field": "TransactionAmt",
        "code": "invalid_numeric",
        "message": "Numeric model features must be finite numbers or numeric strings."
      }
    ]
  }
}
```

## `GET /metrics`

Prometheus exposition format. See `docs/monitoring.md`.

## Authentication

Disabled by default for local development. Set `API_KEY_ENABLED=true` and
`API_KEY=<secret>` to require an `X-API-Key` header on every endpoint
except `/health`, `/ready`, and `/metrics`.

```bash
curl -s -X POST http://localhost:8000/predict \
  -H "X-API-Key: <secret>" \
  -H "Content-Type: application/json" \
  -d '{"records": [{"TransactionAmt": 100.0}]}'
```
