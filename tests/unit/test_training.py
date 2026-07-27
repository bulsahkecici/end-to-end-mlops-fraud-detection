from __future__ import annotations

import numpy as np
import pytest

from src.data.ingest import make_synthetic_transactions
from src.modeling.evaluate import compute_metrics
from src.modeling.threshold import (
    expected_cost,
    find_best_f1_threshold,
    find_cost_based_threshold,
    find_target_recall_threshold,
    select_threshold,
)
from src.modeling.train import run_training
from src.modeling.validation import random_split, split_data, temporal_split

# --- split strategies -----------------------------------------------------


def test_temporal_split_preserves_time_ordering():
    df = make_synthetic_transactions(n=1000, seed=1)
    train, val, test = temporal_split(df, 0.7, 0.15, 0.15)
    assert train["TransactionDT"].max() <= val["TransactionDT"].min()
    assert val["TransactionDT"].max() <= test["TransactionDT"].min()
    assert len(train) + len(val) + len(test) == len(df)


def test_temporal_split_ratios_approximately_correct():
    df = make_synthetic_transactions(n=1000, seed=1)
    train, val, test = temporal_split(df, 0.7, 0.15, 0.15)
    assert abs(len(train) - 700) <= 1
    assert abs(len(val) - 150) <= 1


def test_random_split_is_stratified_roughly():
    df = make_synthetic_transactions(n=2000, seed=1, fraud_rate=0.1)
    train, val, test = random_split(df, 0.7, 0.15, 0.15, seed=1)
    overall_rate = df["isFraud"].mean()
    for split in (train, val, test):
        assert abs(split["isFraud"].mean() - overall_rate) < 0.05


def test_split_data_logs_summary():
    df = make_synthetic_transactions(n=500, seed=2)
    train, val, test, summary = split_data(df, "temporal", 0.7, 0.15, 0.15, seed=2)
    splits = {"train": train, "val": val, "test": test}
    for name, split_df in splits.items():
        assert summary[name]["n_rows"] == len(split_df)
        assert "fraud_rate" in summary[name]
        assert "dt_min" in summary[name] and "dt_max" in summary[name]


def test_split_data_rejects_unknown_strategy():
    df = make_synthetic_transactions(n=100, seed=1)
    with pytest.raises(ValueError):
        split_data(df, "bogus", 0.7, 0.15, 0.15, seed=1)


# --- metrics ---------------------------------------------------------------


def test_compute_metrics_keys_and_ranges():
    rng = np.random.default_rng(0)
    y_true = rng.binomial(1, 0.1, 200)
    y_proba = rng.random(200)
    metrics = compute_metrics(y_true, y_proba, threshold=0.5)
    for key in (
        "roc_auc",
        "pr_auc",
        "precision",
        "recall",
        "f1",
        "log_loss",
        "brier_score",
        "fraud_rate",
    ):
        assert key in metrics
    assert 0.0 <= metrics["precision"] <= 1.0
    assert 0.0 <= metrics["recall"] <= 1.0
    cm = metrics["confusion_matrix"]
    assert cm["tn"] + cm["fp"] + cm["fn"] + cm["tp"] == 200


# --- threshold strategies ---------------------------------------------------


@pytest.fixture
def val_labels_probs():
    rng = np.random.default_rng(3)
    y_true = rng.binomial(1, 0.15, 500)
    # correlate probability with label so thresholding is meaningful
    y_proba = np.clip(y_true * 0.6 + rng.random(500) * 0.4, 0, 1)
    return y_true, y_proba


def test_best_f1_threshold_in_range(val_labels_probs):
    y_true, y_proba = val_labels_probs
    t = find_best_f1_threshold(y_true, y_proba)
    assert 0.0 <= t <= 1.0


def test_target_recall_threshold_achieves_recall(val_labels_probs):
    y_true, y_proba = val_labels_probs
    t = find_target_recall_threshold(y_true, y_proba, target_recall=0.8)
    y_pred = (y_proba >= t).astype(int)
    from sklearn.metrics import recall_score

    assert recall_score(y_true, y_pred) >= 0.8 - 1e-9


def test_cost_based_threshold_beats_fixed_default(val_labels_probs):
    y_true, y_proba = val_labels_probs
    t, cost_at_best = find_cost_based_threshold(y_true, y_proba, fn_cost=25.0, fp_cost=1.0)
    cost_at_half = expected_cost(y_true, (y_proba >= 0.5).astype(int), 25.0, 1.0)
    assert cost_at_best <= cost_at_half


def test_select_threshold_all_strategies(val_labels_probs):
    y_true, y_proba = val_labels_probs
    for strategy in ("fixed", "best_f1", "target_recall", "cost_based"):
        info = select_threshold(y_true, y_proba, strategy=strategy)
        assert 0.0 <= info["threshold"] <= 1.0
        assert info["strategy"] == strategy


def test_select_threshold_rejects_unknown_strategy(val_labels_probs):
    y_true, y_proba = val_labels_probs
    with pytest.raises(ValueError):
        select_threshold(y_true, y_proba, strategy="not_a_strategy")


# --- end-to-end training (synthetic, isolated mlflow store) ----------------


def test_run_training_synthetic_end_to_end(mlflow_tmp_uri):
    result = run_training(
        data_source="synthetic",
        n_synthetic=800,
        seed=42,
        tracking_uri=mlflow_tmp_uri,
        register=True,
    )
    assert result["run_id"]
    assert result["model_version"] == "1"
    assert 0.0 <= result["threshold"] <= 1.0
    assert 0.0 <= result["val_metrics"]["roc_auc"] <= 1.0 or np.isnan(
        result["val_metrics"]["roc_auc"]
    )
    assert result["feature_schema"]["numeric_cols"]
    assert result["feature_schema"]["categorical_cols"]

    import mlflow

    mlflow.set_tracking_uri(mlflow_tmp_uri)
    client = mlflow.MlflowClient()
    mv = client.get_model_version_by_alias("ieee_fraud_lgbm", "candidate")
    assert str(mv.version) == result["model_version"]
