"""
FastAPI inference service for IEEE-CIS fraud model.
Loads model from MLflow registry (alias 'prod') and optional feature_meta.json for column parity.
"""
import json
import os
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
MODEL_NAME = os.getenv("MODEL_NAME", "ieee_fraud_lgbm")
ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "artifacts"

app = FastAPI(title="IEEE Fraud Detection API", version="0.1.0")

model = None
feature_meta = None


class PredictRequest(BaseModel):
    records: list[dict]


@app.on_event("startup")
def load_model() -> None:
    global model, feature_meta
    import mlflow

    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    model_uri = f"models:/{MODEL_NAME}@prod"
    try:
        model = mlflow.pyfunc.load_model(model_uri)
    except Exception as e:
        model = None
        print(f"Model load failed (start server after training): {e}")

    meta_path = ARTIFACTS_DIR / "feature_meta.json"
    if meta_path.exists():
        with open(meta_path) as f:
            feature_meta = json.load(f)
    else:
        feature_meta = None


def _encode_categoricals(df: pd.DataFrame) -> pd.DataFrame:
    """Encode cat cols to int using feature_meta['cat_mappings']; unseen/missing -> -1."""
    if feature_meta is None:
        return df
    cat_mappings = feature_meta.get("cat_mappings") or {}
    out = df.copy()
    for c, categories in cat_mappings.items():
        if c not in out.columns:
            continue
        # map value -> index in categories, else -1
        def code(val):
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return -1
            try:
                idx = categories.index(val)
                return idx
            except (ValueError, TypeError):
                return -1
        out[c] = out[c].map(code).astype("int32")
    return out


def _ensure_columns(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if feature_meta is None:
        return df
    use_cols = feature_meta.get("use_cols")
    if not use_cols:
        return df
    out = df.copy()
    for c in use_cols:
        if c not in out.columns:
            out[c] = None
    out = out[use_cols]
    out = _encode_categoricals(out)
    return out


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model_loaded": model is not None}


@app.post("/predict")
def predict(body: PredictRequest) -> dict:
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    if not body.records:
        return {"predictions": []}
    X = _ensure_columns(body.records)
    preds = model.predict(X)
    if hasattr(preds, "tolist"):
        preds = preds.tolist()
    return {"predictions": [float(p) for p in preds]}
