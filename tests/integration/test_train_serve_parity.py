"""Verifies that predictions from the in-memory training pipeline and
predictions from the model reloaded from MLflow (the same code path the
FastAPI service uses) are numerically identical for the same inputs.

This is the core regression test for the training/serving parity bug the
project used to have (LightGBM-native categorical dtype at train time vs. a
hand-rolled integer mapping at serve time).
"""

from __future__ import annotations

import mlflow
import numpy as np
import pandas as pd

from src.modeling.train import run_training


def test_train_and_reloaded_model_predict_identically(mlflow_tmp_uri):
    result = run_training(
        data_source="synthetic",
        n_synthetic=800,
        seed=99,
        tracking_uri=mlflow_tmp_uri,
        register=True,
        debug_return=True,
    )
    debug = result["_debug"]
    X_val_raw: pd.DataFrame = debug["X_val_raw"]
    val_proba_from_training: np.ndarray = debug["val_proba"]

    mlflow.set_tracking_uri(mlflow_tmp_uri)
    reloaded = mlflow.pyfunc.load_model(
        f"models:/{result['model_name']}@candidate"
    ).unwrap_python_model()

    sample = X_val_raw.head(30).reset_index(drop=True)
    expected_proba = val_proba_from_training[:30]

    served_output = reloaded.predict(None, sample)
    served_proba = served_output["fraud_probability"].to_numpy()

    np.testing.assert_allclose(served_proba, expected_proba, rtol=1e-6, atol=1e-8)


def test_reloaded_model_handles_missing_and_reordered_columns_like_training(mlflow_tmp_uri):
    result = run_training(
        data_source="synthetic",
        n_synthetic=600,
        seed=11,
        tracking_uri=mlflow_tmp_uri,
        register=True,
        debug_return=True,
    )
    debug = result["_debug"]
    X_val_raw: pd.DataFrame = debug["X_val_raw"]

    mlflow.set_tracking_uri(mlflow_tmp_uri)
    reloaded = mlflow.pyfunc.load_model(
        f"models:/{result['model_name']}@candidate"
    ).unwrap_python_model()

    row = X_val_raw.iloc[[0]]
    full_pred = reloaded.predict(None, row)["fraud_probability"].iloc[0]

    # Reorder columns -> must be identical.
    reordered = row[list(reversed(row.columns))]
    reordered_pred = reloaded.predict(None, reordered)["fraud_probability"].iloc[0]
    assert reordered_pred == full_pred

    # Drop half the columns -> must not crash, must return a valid probability.
    partial = row[row.columns[: len(row.columns) // 2]]
    partial_pred = reloaded.predict(None, partial)["fraud_probability"].iloc[0]
    assert 0.0 <= partial_pred <= 1.0


def test_calibrated_frequency_pipeline_survives_mlflow_round_trip(mlflow_tmp_uri):
    result = run_training(
        data_source="synthetic",
        n_synthetic=4000,
        seed=17,
        tracking_uri=mlflow_tmp_uri,
        register=True,
        categorical_strategy="frequency_high_cardinality",
        calibration_strategy="sigmoid",
        cat_nunique_max=2,
        debug_return=True,
    )
    sample = result["_debug"]["X_val_raw"].head(25).reset_index(drop=True)
    expected = result["_debug"]["pipeline"].predict_proba(sample)[:, 1]

    mlflow.set_tracking_uri(mlflow_tmp_uri)
    reloaded = mlflow.pyfunc.load_model(
        f"models:/{result['model_name']}@candidate"
    ).unwrap_python_model()
    actual = reloaded.predict(None, sample)["fraud_probability"].to_numpy()

    np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-8)
    assert result["feature_schema"]["high_cardinality_cols"]
