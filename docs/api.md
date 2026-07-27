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
  objects with arbitrary keys.
- **Only `TransactionAmt` is meaningfully required** in the sense that it's
  the one column present in every README example — in practice *every*
  column is optional. Missing columns are imputed the same way the
  training pipeline imputes missing values; unknown/extra columns are
  dropped; column order doesn't matter; categories never seen in training
  map to a reserved "unknown" code instead of raising.

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
| Request body larger than `API_MAX_REQUEST_BYTES` | `413` | `{"detail": "Request body too large"}` |
| No model loaded | `503` | `{"detail": "Model not loaded. ..."}` |
| Missing/invalid `X-API-Key` (only when `API_KEY_ENABLED=true`) | `401` | `{"detail": "Invalid or missing API key"}` |
| Unexpected internal error | `400` (prediction path) or `500` (unhandled) | generic `{"detail": ...}` — never a raw traceback |

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
