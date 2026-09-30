from __future__ import annotations

from types import SimpleNamespace

import mlflow
import pytest
from mlflow.exceptions import MlflowException

from src.config import settings
from src.modeling.train import run_training
from src.registry.compare import (
    PromotionComparisonError,
    RegistryLookupError,
    compare_candidate_vs_champion,
    compare_model_versions,
    get_alias_model_version,
)
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
    assert result["comparison"]["evaluation"]["fingerprint"]
    assert result["comparison"]["candidate"]["metrics"]["n_samples"] > 0
    assert result["deployment_changed"] is False
    artifacts = client.list_artifacts(result["comparison"]["candidate"]["run_id"], "promotion")
    assert any(item.path == "promotion/promotion_decision.json" for item in artifacts)


def test_promote_blocks_when_pr_auc_gate_fails(mlflow_tmp_uri, monkeypatch):
    monkeypatch.setattr(settings, "min_pr_auc", 0.999)  # unreachable on purpose
    run_training(data_source="synthetic", n_synthetic=800, seed=2, tracking_uri=mlflow_tmp_uri)

    result = run_promotion_checks(tracking_uri=mlflow_tmp_uri)
    assert result["promoted"] is False
    assert result["checks"]["min_pr_auc"]["passed"] is False

    mlflow.set_tracking_uri(mlflow_tmp_uri)
    client = mlflow.MlflowClient()
    with pytest.raises(MlflowException):
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
    report = compare_candidate_vs_champion(
        model_name="never_trained_model", tracking_uri=mlflow_tmp_uri
    )
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

    run_training(
        data_source="synthetic",
        n_synthetic=1500,
        seed=5,
        tracking_uri=mlflow_tmp_uri,
        lgbm_overrides={"num_leaves": 15},
    )  # v2 -> candidate, same frozen evaluation rows

    report = compare_candidate_vs_champion(tracking_uri=mlflow_tmp_uri)
    assert report["champion"] is not None
    assert report["candidate"]["version"] != report["champion"]["version"]
    assert "pr_auc" in report["diff"]
    assert "expected_cost" in report["diff"]
    assert report["candidate"]["metrics"]["n_samples"] == report["champion"]["metrics"]["n_samples"]
    assert report["evaluation"]["fingerprint"]
    assert report["candidate"]["provenance"]["git"]
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


def test_mismatched_candidate_champion_fingerprints_block_and_preserve_champion(
    mlflow_tmp_uri, monkeypatch
):
    monkeypatch.setattr(settings, "min_pr_auc", 0.0)
    monkeypatch.setattr(settings, "min_recall", 0.0)
    first = run_training(
        data_source="synthetic", n_synthetic=1200, seed=10, tracking_uri=mlflow_tmp_uri
    )
    assert run_promotion_checks(tracking_uri=mlflow_tmp_uri)["promoted"] is True
    run_training(data_source="synthetic", n_synthetic=1200, seed=11, tracking_uri=mlflow_tmp_uri)

    result = run_promotion_checks(tracking_uri=mlflow_tmp_uri)
    assert result["promoted"] is False
    assert "does not match" in result["comparison"]["reason"]
    client = mlflow.MlflowClient()
    champion = client.get_model_version_by_alias(settings.model_name, settings.champion_alias)
    assert str(champion.version) == first["model_version"]


def test_missing_evaluation_evidence_blocks_comparison(mlflow_tmp_uri, monkeypatch):
    run_training(data_source="synthetic", n_synthetic=800, seed=12, tracking_uri=mlflow_tmp_uri)

    def missing_evidence(_model_name, _version):
        return None, object(), {"threshold": 0.5, "cost": {}}

    monkeypatch.setattr("src.registry.compare._load_version", missing_evidence)
    report = compare_candidate_vs_champion(tracking_uri=mlflow_tmp_uri)
    assert report["decision"] == "blocked"
    assert "missing promotion evaluation evidence" in report["reason"]


def test_model_load_failure_blocks_promotion(mlflow_tmp_uri, monkeypatch):
    run_training(data_source="synthetic", n_synthetic=800, seed=13, tracking_uri=mlflow_tmp_uri)

    def fail_load(_uri):
        raise OSError("model artifact unavailable")

    monkeypatch.setattr("src.registry.promote.mlflow.pyfunc.load_model", fail_load)
    result = run_promotion_checks(tracking_uri=mlflow_tmp_uri)
    assert result["promoted"] is False
    assert result["checks"]["model_loadable"]["passed"] is False


def test_champion_load_failure_blocks_comparison(monkeypatch):
    candidate = SimpleNamespace(version="2", run_id="candidate-run")
    champion = SimpleNamespace(version="1", run_id="champion-run")

    def fail_for_champion(_model_name, version):
        if version == "1":
            raise PromotionComparisonError("champion artifact unavailable")
        return None, object(), {"promotion_evaluation": {}}

    monkeypatch.setattr("src.registry.compare._load_version", fail_for_champion)
    monkeypatch.setattr("src.registry.compare._manifest", lambda _metadata, _version: {})
    report = compare_model_versions(object(), "model", candidate, champion)
    assert report["decision"] == "blocked"
    assert "champion artifact unavailable" in report["reason"]


def test_registry_error_is_not_treated_as_missing_alias():
    class BrokenClient:
        def get_model_version_by_alias(self, _name, _alias):
            raise MlflowException("registry unavailable", error_code="INTERNAL_ERROR")

    with pytest.raises(RegistryLookupError, match="registry unavailable"):
        get_alias_model_version(BrokenClient(), "model", "champion")


def test_recall_gate_uses_rescored_promotion_metrics(mlflow_tmp_uri, monkeypatch):
    monkeypatch.setattr(settings, "min_pr_auc", 0.0)
    monkeypatch.setattr(settings, "min_recall", 1.1)
    run_training(data_source="synthetic", n_synthetic=1000, seed=14, tracking_uri=mlflow_tmp_uri)
    result = run_promotion_checks(tracking_uri=mlflow_tmp_uri)
    assert result["promoted"] is False
    assert result["checks"]["min_recall"]["passed"] is False
    assert "actual" in result["checks"]["min_recall"]["detail"]


def test_regression_gate_blocks_worse_candidate_and_preserves_champion(mlflow_tmp_uri, monkeypatch):
    monkeypatch.setattr(settings, "min_pr_auc", 0.0)
    monkeypatch.setattr(settings, "min_recall", 0.0)
    monkeypatch.setattr(settings, "max_champion_regression", 0.0)
    first = run_training(
        data_source="synthetic", n_synthetic=1200, seed=15, tracking_uri=mlflow_tmp_uri
    )
    assert run_promotion_checks(tracking_uri=mlflow_tmp_uri)["promoted"] is True
    run_training(
        data_source="synthetic",
        n_synthetic=1200,
        seed=15,
        tracking_uri=mlflow_tmp_uri,
        lgbm_overrides={"n_estimators": 1, "num_leaves": 2},
    )

    result = run_promotion_checks(tracking_uri=mlflow_tmp_uri)
    assert result["promoted"] is False
    assert result["checks"]["max_champion_regression"]["passed"] is False
    client = mlflow.MlflowClient()
    champion = client.get_model_version_by_alias(settings.model_name, settings.champion_alias)
    assert str(champion.version) == first["model_version"]


def test_candidate_alias_movement_blocks_promotion(mlflow_tmp_uri, monkeypatch):
    monkeypatch.setattr(settings, "min_pr_auc", 0.0)
    monkeypatch.setattr(settings, "min_recall", 0.0)
    trained = run_training(
        data_source="synthetic", n_synthetic=1000, seed=16, tracking_uri=mlflow_tmp_uri
    )
    real_get = get_alias_model_version
    candidate_lookups = 0

    def moving_alias(client, model_name, alias):
        nonlocal candidate_lookups
        if alias == settings.candidate_alias:
            candidate_lookups += 1
            if candidate_lookups == 2:
                return SimpleNamespace(version="999", run_id="moved")
        return real_get(client, model_name, alias)

    monkeypatch.setattr("src.registry.promote.get_alias_model_version", moving_alias)
    result = run_promotion_checks(tracking_uri=mlflow_tmp_uri)
    assert result["promoted"] is False
    assert result["checks"]["candidate_alias_stable"]["passed"] is False
    client = mlflow.MlflowClient()
    with pytest.raises(MlflowException):
        client.get_model_version_by_alias(settings.model_name, settings.champion_alias)
    assert result["candidate_version"] == trained["model_version"]
