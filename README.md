# End-to-End MLOps: IEEE-CIS Fraud Detection

A CV-ready, minimal MLOps demo using the **IEEE-CIS Fraud Detection (Vesta)** dataset: ingest → feature pipeline → train (LightGBM) → register to MLflow Model Registry (alias `prod`) → FastAPI inference. Designed to run on **8GB RAM** (sampling + type optimization) and on **Ubuntu** and **Windows** with the same setup.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  MLflow (Docker)                                                         │
│  - Backend: sqlite:///db/mlflow.db                                        │
│  - Artifacts: /db/artifacts (--serve-artifacts)                           │
│  - Volume: ./mlflow_db:/db                                                │
└─────────────────────────────────────────────────────────────────────────┘
         │
         │ MLFLOW_TRACKING_URI=http://localhost:5000
         ▼
┌──────────────────────┐     ┌──────────────────────┐
│  Train pipeline      │     │  FastAPI serve        │
│  src/train_ieee.py   │────▶│  src/serve/app.py     │
│  - Ingest (sample)   │     │  - models:/...@prod   │
│  - Features          │     │  - GET /health        │
│  - LightGBM + MLflow │     │  - POST /predict      │
└──────────────────────┘     └──────────────────────┘
```

## Quickstart (Ubuntu / Windows)

**1. Environment**

```bash
conda create -n fraudmlops python=3.11
conda activate fraudmlops
pip install -r requirements.txt
```

**2. MLflow server (single volume to avoid permission issues)**

```bash
docker compose up -d
```

**3. Data**

Download the [IEEE-CIS Fraud Detection](https://www.kaggle.com/c/ieee-fraud-detection/data) dataset from Kaggle and place the CSVs into:

- `data/processed/ieee-fraud-detection/train_transaction.csv`
- `data/processed/ieee-fraud-detection/train_identity.csv`
- `data/processed/ieee-fraud-detection/test_transaction.csv`
- `data/processed/ieee-fraud-detection/test_identity.csv`

**4. Train (from repo root)**

```bash
export PYTHONPATH=.
python src/train_ieee.py
```

**5. Serve**

```bash
uvicorn src.serve.app:app --host 0.0.0.0 --port 8000
```

**6. Call API**

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/predict -H "Content-Type: application/json" -d '{"records":[{"TransactionAmt":1.0}]}'
```

## 8GB RAM and sampling

- Training uses **300,000** transaction rows by default (`SAMPLE_ROWS=300000`) so it fits in 8GB.
- To improve performance, increase sample size (e.g. `SAMPLE_ROWS=500000`) or use full data if you have more RAM.

## Configuration (env)

| Variable               | Default              | Description                    |
|------------------------|----------------------|--------------------------------|
| `MLFLOW_TRACKING_URI`  | `http://localhost:5000` | MLflow server URL           |
| `MODEL_NAME`           | `ieee_fraud_lgbm`    | Registered model name          |
| `SAMPLE_ROWS`          | `300000`             | Max transaction rows for train |

## Troubleshooting

- **Artifact permission errors**  
  If the server previously used a different artifact path (e.g. `/mlruns`), reset the store and artifacts: delete the `mlflow_db` folder and run `docker compose up -d` again. Then re-run training.

- **LightGBM flavor with mlflow-skinny**  
  If you see missing flavor imports for LightGBM, switch to the full MLflow client: in `requirements.txt` use `mlflow==2.16.2` instead of `mlflow-skinny==2.16.2`.

## Validate end-to-end

```bash
docker compose up -d
PYTHONPATH=. python src/train_ieee.py
uvicorn src.serve.app:app --port 8000
# In another terminal:
curl http://localhost:8000/health
curl -X POST http://localhost:8000/predict -H "Content-Type: application/json" -d '{"records":[{"TransactionAmt":1.0}]}'
```

## License

MIT.
