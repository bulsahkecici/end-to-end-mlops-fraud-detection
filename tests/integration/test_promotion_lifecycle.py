from __future__ import annotations

import mlflow

from src.config import settings
from src.modeling.train import run_training
from src.registry.promote import run_promotion_checks


def test_two_versions_are_rescored_on_the_same_frozen_rows(mlflow_tmp_uri, monkeypatch):
    monkeypatch.setattr(settings, "min_pr_auc", 0.0)
    monkeypatch.setattr(settings, "min_recall", 0.0)
    monkeypatch.setattr(settings, "max_champion_regression", 1.0)

    first = run_training(
        data_source="synthetic",
        n_synthetic=1600,
        seed=101,
        tracking_uri=mlflow_tmp_uri,
    )
    assert run_promotion_checks(tracking_uri=mlflow_tmp_uri)["promoted"] is True

    second = run_training(
        data_source="synthetic",
        n_synthetic=1600,
        seed=101,
        tracking_uri=mlflow_tmp_uri,
        lgbm_overrides={"num_leaves": 7, "learning_rate": 0.02},
    )
    result = run_promotion_checks(tracking_uri=mlflow_tmp_uri)

    assert result["promoted"] is True
    comparison = result["comparison"]
    assert comparison["candidate"]["version"] == second["model_version"]
    assert comparison["champion"]["version"] == first["model_version"]
    assert comparison["evaluation"]["fingerprint"] == second["promotion_evaluation"]["fingerprint"]
    assert (
        comparison["candidate"]["metrics"]["n_samples"]
        == comparison["champion"]["metrics"]["n_samples"]
    )
    assert "expected_cost" in comparison["candidate"]["metrics"]
    assert "expected_cost" in comparison["champion"]["metrics"]

    mlflow.set_tracking_uri(mlflow_tmp_uri)
    champion = mlflow.MlflowClient().get_model_version_by_alias(
        settings.model_name, settings.champion_alias
    )
    assert str(champion.version) == second["model_version"]


def test_sigmoid_calibrated_candidate_can_be_promoted_loaded_and_scored(
    mlflow_tmp_uri, monkeypatch
):
    monkeypatch.setattr(settings, "min_pr_auc", 0.0)
    monkeypatch.setattr(settings, "min_recall", 0.0)

    result = run_training(
        data_source="synthetic",
        n_synthetic=4000,
        seed=117,
        tracking_uri=mlflow_tmp_uri,
        calibration_strategy="sigmoid",
        debug_return=True,
    )
    promotion = run_promotion_checks(tracking_uri=mlflow_tmp_uri)

    assert promotion["promoted"] is True
    assert promotion["candidate_version"] == result["model_version"]
    assert result["promotion_evaluation"]["excludes_final_test"] is True

    mlflow.set_tracking_uri(mlflow_tmp_uri)
    loaded = mlflow.pyfunc.load_model(f"models:/{settings.model_name}@{settings.champion_alias}")
    wrapper = loaded.unwrap_python_model()
    assert wrapper.metadata["experiment"]["calibration_strategy"] == "sigmoid"

    sample = result["_debug"]["X_val_raw"].head(4).copy()
    for column in result["feature_schema"]["numeric_cols"]:
        sample[column] = sample[column].astype("float64")
    predictions = loaded.predict(sample)
    assert len(predictions) == 4
    assert predictions["fraud_probability"].between(0.0, 1.0).all()
