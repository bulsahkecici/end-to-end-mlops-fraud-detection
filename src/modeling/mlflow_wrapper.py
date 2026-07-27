"""Custom MLflow pyfunc wrapper around the sklearn fraud-detection pipeline.

Wrapping is necessary because the plain ``mlflow.sklearn`` pyfunc flavor
calls ``predict()`` on the underlying estimator, which for a classifier
returns a hard 0/1 label rather than the fraud probability the API needs.
This wrapper calls ``predict_proba`` internally and returns both the
probability and the thresholded decision, so training and serving always
agree on what "predict" means for this model.
"""
from __future__ import annotations

import json

import mlflow.pyfunc
import pandas as pd


class FraudModelWrapper(mlflow.pyfunc.PythonModel):
    """MLflow PythonModel wrapping a fitted sklearn Pipeline + decision threshold."""

    def load_context(self, context) -> None:
        import joblib

        self.pipeline = joblib.load(context.artifacts["pipeline"])
        with open(context.artifacts["metadata"]) as f:
            self.metadata = json.load(f)
        self.default_threshold = float(self.metadata.get("threshold", 0.5))

    def predict(self, context, model_input: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
        if not isinstance(model_input, pd.DataFrame):
            model_input = pd.DataFrame(model_input)
        threshold = self.default_threshold
        if params and params.get("threshold") is not None:
            threshold = float(params["threshold"])

        proba = self.pipeline.predict_proba(model_input)[:, 1]
        prediction = (proba >= threshold).astype(int)
        return pd.DataFrame(
            {
                "fraud_probability": proba,
                "fraud_prediction": prediction,
                "threshold": threshold,
            }
        )
