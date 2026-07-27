# End-to-End MLOps: IEEE-CIS Fraud Detection

A CV-ready, end-to-end MLOps project on the **IEEE-CIS Fraud Detection (Vesta)** dataset: ingest → validate → temporal split → single shared preprocessing+model pipeline (LightGBM) → MLflow Model Registry (`candidate` → `champion` promotion gate) → FastAPI inference → Docker → CI.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  MLflow (tracking + model registry)                                     │
│  local-lite profile: sqlite + local artifact dir                        │
│  production-like profile: Postgres backend + MinIO (S3-compatible)      │
└─────────────────────────────────────────────────────────────────────────┘
         │ MLFLOW_TRACKING_URI
         ▼
┌────────────────────────┐        ┌──────────────────────────┐
│ src/modeling/train.py   │        │ src/api/app.py            │
│ ingest → validate       │───────▶│ loads models:/<name>@champion (or
│ → temporal split        │  gate  │ legacy "Production" stage)│
│ → fit ColumnAligner +   │ (P1.5) │ GET  /health  /ready       │
│   ColumnTransformer +   │        │ POST /predict              │
│   LGBMClassifier        │        │ GET  /metrics (Prometheus) │
│ → log ONE pyfunc model  │        └──────────────────────────┘
│   (registers `candidate`)│
└────────────────────────┘
```

The critical design point: **training and serving share one fitted `sklearn.Pipeline` object**, logged to MLflow as a single artifact (`src/features/pipeline.py` + `src/modeling/mlflow_wrapper.py`). There is no separate local `feature_meta.json` and no hand-rolled category→int mapping on the serving side — the exact fitted `ColumnAligner` + `ColumnTransformer` that ran at training time also runs inside the API process, so a request can never be scored differently than an equivalent row was during training/validation.

## Quickstart (local-lite, no Docker required)

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

# 1. Start MLflow locally (sqlite backend, no Docker needed)
export MLFLOW_TRACKING_URI="sqlite:///$(pwd)/mlflow_db/mlflow.db"   # PowerShell: $env:MLFLOW_TRACKING_URI="sqlite:///$PWD/mlflow_db/mlflow.db"

# 2. Train (synthetic data — no Kaggle download needed)
python -m src.modeling.train --data-source synthetic --n-synthetic 4000

# 3. Promote the new model from `candidate` to `champion` (gated — see docs/deployment.md)
python -m src.registry.promote

# 4. Serve
python -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000

# 5. Call the API (see "API contract" below)
curl -s http://localhost:8000/health
curl -s http://localhost:8000/ready
curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"records": [{"TransactionAmt": 100.0}]}'
```

## Quickstart with the real IEEE-CIS dataset

Download the [IEEE-CIS Fraud Detection](https://www.kaggle.com/c/ieee-fraud-detection/data) dataset from Kaggle and place the CSVs into:

```
data/processed/ieee-fraud-detection/train_transaction.csv
data/processed/ieee-fraud-detection/train_identity.csv
data/processed/ieee-fraud-detection/test_transaction.csv
data/processed/ieee-fraud-detection/test_identity.csv
```

Then:

```bash
cp .env.example .env   # docker compose needs this to parse the file regardless of profile
docker compose --profile local-lite up -d          # or --profile production-like, see docs/deployment.md
python -m src.modeling.train --data-source ieee --sample-rows 300000
python -m src.registry.promote
python -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000   # or just use the `api`/`api-prod` container
```

The `docker compose` commands above also start `nginx`, a rate-limited
reverse proxy in front of the API on `http://localhost:8080` (see
"Rate limiting" in `docs/deployment.md`) — hit that instead of `:8000`
directly if you want the rate limit enforced.

`--sample-rows` uses **deterministic, time-span-preserving sampling** (`SAMPLING_STRATEGY=time_ordered` by default), not a `nrows=N` head-of-file read — see `src/data/sampling.py`.

## API contract

### `GET /health`
Liveness only — the process is up. Always `200` once the server has started.

### `GET /ready`
Readiness — a model is loaded from the registry and can serve predictions. `200` when ready, `503` with an explanatory `detail` when no model is loaded yet (train + promote first).

### `POST /predict`

Request:

```json
{
  "records": [
    { "TransactionAmt": 100.0 }
  ]
}
```

**Every field besides `TransactionAmt` is optional.** The service tolerates, in any combination:
- missing columns (imputed with the training-time median/most-common value),
- extra/unknown columns (dropped),
- reordered columns,
- categories never seen during training (mapped to an "unknown" code),
- a single record or a batch (up to `API_MAX_BATCH_SIZE`, default 500).

An empty `records` list or malformed JSON returns `422`. A request larger than the configured batch/body limits returns `422`/`413`. If no model is loaded, every endpoint except `/health` returns `503`. Internal errors are never surfaced as raw tracebacks — always a generic `4xx`/`5xx` JSON body.

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

`threshold` is the decision threshold selected on the validation set at training time (see `src/modeling/threshold.py`), not a hardcoded `0.5`.

## Configuration (env)

All configuration is centralized in `src/config.py`. Key variables (full list in `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `MLFLOW_TRACKING_URI` | `http://localhost:5000` | MLflow server URL |
| `MODEL_NAME` | `ieee_fraud_lgbm` | Registered model name |
| `CHAMPION_ALIAS` / `CANDIDATE_ALIAS` | `champion` / `candidate` | Registry aliases used for promotion |
| `SAMPLE_ROWS` | `300000` | Max transaction rows for `--data-source ieee` training |
| `SAMPLING_STRATEGY` | `time_ordered` | `time_ordered` (deterministic, span-preserving) or `random` |
| `SPLIT_STRATEGY` | `temporal` | `temporal` (default) or `random` (explicit smoke-test fallback) |
| `THRESHOLD_STRATEGY` | `best_f1` | `best_f1` \| `target_recall` \| `cost_based` \| `fixed` |
| `FALSE_NEGATIVE_COST` / `FALSE_POSITIVE_COST` | `25.0` / `1.0` | Used by the `cost_based` threshold strategy |
| `API_MAX_BATCH_SIZE` | `500` | Max records per `/predict` request |
| `API_KEY_ENABLED` / `API_KEY` | `false` / unset | Optional API-key auth (disabled by default for local dev) |
| `RATE_LIMIT_RPS` / `RATE_LIMIT_BURST` | `10` / `20` | nginx reverse-proxy rate limit (requests/sec/IP, burst) — see `docs/deployment.md` |

## Project layout

See `docs/architecture.md` for a full description. Top level:

```
src/config.py           single source of truth for all paths/settings
src/data/                ingest, deterministic sampling, structural validation
src/features/pipeline.py the shared ColumnAligner + ColumnTransformer
src/modeling/             temporal split, metrics, threshold selection, training entrypoint
src/registry/             promote.py (candidate->champion gate), compare.py (model diff report)
src/api/                  FastAPI service
src/monitoring/drift.py   reference-vs-batch drift report
tests/unit, tests/integration
```

## Running tests

```bash
pip install -r requirements-dev.txt
pytest                       # full suite, synthetic fixtures only — no real data needed
pytest --cov=src --cov-report=term-missing
```

## Troubleshooting

- **"Model not loaded" / `/ready` returns 503`** — train a model and run `python -m src.registry.promote` before starting the API; the service only ever loads the `champion` alias (or, for backward compatibility, the legacy `Production` stage).
- **Artifact permission errors (Docker profile)** — delete the `mlflow_db` / `minio_data` volumes and re-run `docker compose up -d`.

## License

MIT.
