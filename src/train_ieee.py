"""
IEEE-CIS Fraud Detection training pipeline: LightGBM + MLflow.
Register model with alias 'prod'. Run with MLflow server up (e.g. docker compose up -d).
"""
import json
import os
from pathlib import Path

import lightgbm as lgb
import mlflow
import mlflow.lightgbm
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import train_test_split

from src.data.ingest import load_train
from src.features.build import build_features

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
MODEL_NAME = os.getenv("MODEL_NAME", "ieee_fraud_lgbm")
SAMPLE_ROWS = int(os.environ.get("SAMPLE_ROWS", "300000"))
ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment("ieee-cis-train")

    df = load_train(sample_rows=SAMPLE_ROWS)
    X, y, meta = build_features(df)
    assert y is not None

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    cat_cols = meta["cat_cols"]
    cat_idx = [list(X.columns).index(c) for c in cat_cols if c in X.columns]
    lgb_train = lgb.Dataset(X_train, label=y_train, categorical_feature=cat_idx)
    lgb_val = lgb.Dataset(X_val, label=y_val, categorical_feature=cat_idx, reference=lgb_train)

    params = {
        "objective": "binary",
        "metric": "auc",
        "verbosity": -1,
        "boosting_type": "gbdt",
        "num_leaves": 31,
        "learning_rate": 0.05,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "seed": 42,
    }

    with mlflow.start_run(run_name="ieee-lgbm"):
        mlflow.log_params(params)
        mlflow.log_param("sample_rows", SAMPLE_ROWS)
        mlflow.log_param("n_train", len(X_train))
        mlflow.log_param("n_val", len(X_val))

        model = lgb.train(
            params,
            lgb_train,
            num_boost_round=500,
            valid_sets=[lgb_val],
            valid_names=["val"],
            callbacks=[lgb.early_stopping(stopping_rounds=20, verbose=False)],
        )

        proba = model.predict(X_val)
        roc_auc = roc_auc_score(y_val, proba)
        pr_auc = average_precision_score(y_val, proba)
        mlflow.log_metric("roc_auc", float(roc_auc))
        mlflow.log_metric("pr_auc", float(pr_auc))

        meta_path = ARTIFACTS_DIR / "feature_meta.json"
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
        mlflow.log_artifact(str(meta_path), artifact_path="feature_meta")

        mlflow.lightgbm.log_model(
            model,
            artifact_path="model",
            registered_model_name=MODEL_NAME,
        )

        client = mlflow.MlflowClient()
        run_id = mlflow.active_run().info.run_id
        versions = client.search_model_versions(f"run_id='{run_id}'")
        version = str(versions[0].version) if versions else None
        if version:
            try:
                client.set_registered_model_alias(MODEL_NAME, "prod", version)
            except Exception:
                client.transition_model_version_stage(
                    name=MODEL_NAME,
                    version=version,
                    stage="Production",
                )

    if version:
        print(f"Registered model {MODEL_NAME} version {version} with alias 'prod' (or Production).")
    print(f"ROC-AUC: {roc_auc:.4f}, PR-AUC: {pr_auc:.4f}")


if __name__ == "__main__":
    main()
