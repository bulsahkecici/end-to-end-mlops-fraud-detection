from __future__ import annotations

import mlflow
import pytest

from src.config import settings
from src.modeling.train import run_training
from src.registry.compare import compare_candidate_vs_champion
from src.registry.promote import run_promotion_checks


def test_promote_first_model_with_good_metrics_succeeds(mlflow_tmp_uri, monkeypatch):
    monkeypatch.setattr(settings, "min_pr_auc", 0.0)
    monkeypatch.setattr(settings, "min_recall", 0.0)
    run_training(data_source="synthetic", n_synthetic=1500, seed=1, tracking_uri=mlflow_tmp_uri)

    result = run_promotion_checks(tracking_uri=mlflow_tmp_uri)
    assert result["promoted"] is True
    assert all(c["passed"] for c in result["checks"].values())

    mlflow.set_tracking_uri(mlflow_tmp_uri)
    client = mlflow.MlflowClient()
    mv = client.get_model_version_by_alias(settings.model_name, settings.champion_alias)
    assert str(mv.version) == result["candidate_version"]


def test_promote_blocks_when_pr_auc_gate_fails(mlflow_tmp_uri, monkeypatch):
    monkeypatch.setattr(settings, "min_pr_auc", 0.999)  # unreachable on purpose
    run_training(data_source="synthetic", n_synthetic=800, seed=2, tracking_uri=mlflow_tmp_uri)

    result = run_promotion_checks(tracking_uri=mlflow_tmp_uri)
    assert result["promoted"] is False
    assert result["checks"]["min_pr_auc"]["passed"] is False

    mlflow.set_tracking_uri(mlflow_tmp_uri)
    client = mlflow.MlflowClient()
    with pytest.raises(Exception):
        client.get_model_version_by_alias(settings.model_name, settings.champion_alias)


def test_promote_is_idempotent(mlflow_tmp_uri, monkeypatch):
    monkeypatch.setattr(settings, "min_pr_auc", 0.0)
    monkeypatch.setattr(settings, "min_recall", 0.0)
    run_training(data_source="synthetic", n_synthetic=1200, seed=3, tracking_uri=mlflow_tmp_uri)

    first = run_promotion_checks(tracking_uri=mlflow_tmp_uri)
    second = run_promotion_checks(tracking_uri=mlflow_tmp_uri)
    assert first["promoted"] is True
    # Re-running promote on the already-promoted candidate must not error or
    # change the outcome.
    assert second["candidate_version"] == first["candidate_version"]


def test_promote_no_candidate_returns_clean_failure(mlflow_tmp_uri):
    result = run_promotion_checks(model_name="never_trained_model", tracking_uri=mlflow_tmp_uri)
    assert result["promoted"] is False
    assert result["checks"]["candidate_exists"]["passed"] is False


def test_compare_with_no_models_reports_no_candidate(mlflow_tmp_uri):
    report = compare_candidate_vs_champion(model_name="never_trained_model", tracking_uri=mlflow_tmp_uri)
    assert report["decision"] == "no_candidate"
    assert report["candidate"] is None
    assert report["champion"] is None


def test_compare_candidate_only_recommends_promotion(mlflow_tmp_uri, monkeypatch):
    monkeypatch.setattr(settings, "min_pr_auc", 0.0)
    monkeypatch.setattr(settings, "min_recall", 0.0)
    run_training(data_source="synthetic", n_synthetic=800, seed=4, tracking_uri=mlflow_tmp_uri)

    report = compare_candidate_vs_champion(tracking_uri=mlflow_tmp_uri)
    assert report["decision"] == "promote_candidate_no_champion"
    assert report["candidate"] is not None
    assert report["champion"] is None


def test_compare_reports_threshold_and_metric_diffs(mlflow_tmp_uri, monkeypatch):
    monkeypatch.setattr(settings, "min_pr_auc", 0.0)
    monkeypatch.setattr(settings, "min_recall", 0.0)
    run_training(data_source="synthetic", n_synthetic=1500, seed=5, tracking_uri=mlflow_tmp_uri)
    run_promotion_checks(tracking_uri=mlflow_tmp_uri)  # promotes v1 to champion

    run_training(data_source="synthetic", n_synthetic=1500, seed=6, tracking_uri=mlflow_tmp_uri)  # v2 -> candidate

    report = compare_candidate_vs_champion(tracking_uri=mlflow_tmp_uri)
    assert report["champion"] is not None
    assert report["candidate"]["version"] != report["champion"]["version"]
    assert "val_pr_auc" in report["diff"]
    assert report["decision"] in ("candidate_at_least_as_good", "candidate_worse")


def test_save_reports_writes_json_and_markdown(mlflow_tmp_uri, monkeypatch, tmp_path):
    from src.registry.compare import save_reports

    monkeypatch.setattr(settings, "reports_dir", tmp_path / "reports")
    run_training(data_source="synthetic", n_synthetic=600, seed=7, tracking_uri=mlflow_tmp_uri)
    report = compare_candidate_vs_champion(tracking_uri=mlflow_tmp_uri)

    json_path, md_path = save_reports(report)
    assert json_path.exists()
    assert md_path.exists()
    assert "Model comparison report" in md_path.read_text()
