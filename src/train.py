import os
import numpy as np
import pandas as pd
import mlflow
import mlflow.sklearn
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score
from sklearn.ensemble import HistGradientBoostingClassifier

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")

def main():
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment("fraud-smoke")

    rng = np.random.default_rng(42)
    n = 5000
    X = pd.DataFrame({
        "f1": rng.normal(size=n),
        "f2": rng.normal(size=n),
        "f3": rng.integers(0, 10, size=n),
    })
    y = (rng.random(n) < 0.03).astype(int)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = HistGradientBoostingClassifier(random_state=42)

    with mlflow.start_run(run_name="smoke-train"):
        mlflow.log_param("model", "HistGradientBoostingClassifier")
        mlflow.log_param("n_train", len(X_train))
        mlflow.log_param("n_test", len(X_test))

        model.fit(X_train, y_train)
        proba = model.predict_proba(X_test)[:, 1]
        auc = roc_auc_score(y_test, proba)
        mlflow.log_metric("roc_auc", float(auc))

        mlflow.sklearn.log_model(model, artifact_path="model")

if __name__ == "__main__":
    main()
