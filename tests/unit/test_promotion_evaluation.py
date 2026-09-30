from __future__ import annotations

import hashlib
import shutil
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.modeling.promotion_evaluation import (
    DATA_FILE,
    PromotionEvaluationError,
    load_and_verify_evaluation,
    semantic_evidence_identity,
    split_selection_and_promotion,
    write_evaluation_artifact,
)
from src.registry.compare import PromotionComparisonError, _score_model, compare_model_versions


def _rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "TransactionID": range(100, 110),
            "TransactionDT": range(10),
            "TransactionAmt": np.linspace(1.0, 10.0, 10),
            "isFraud": [0, 1] * 5,
        }
    )


def _write(rows: pd.DataFrame, path):
    return write_evaluation_artifact(
        rows,
        path,
        data_source="synthetic",
        split_strategy="temporal",
        source_data_fingerprint={"source": "fixture-v1"},
        random_seed=42,
    )


def _file_sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compare_manifests(monkeypatch, candidate_manifest, champion_manifest):
    candidate = SimpleNamespace(version="2", run_id="candidate-run")
    champion = SimpleNamespace(version="1", run_id="champion-run")

    def load_version(_model_name, version):
        manifest = candidate_manifest if version == "2" else champion_manifest
        return None, object(), {"promotion_evaluation": manifest}

    monkeypatch.setattr("src.registry.compare._load_version", load_version)
    return compare_model_versions(object(), "model", candidate, champion)


def test_temporal_promotion_rows_are_after_selection_rows():
    selection, promotion = split_selection_and_promotion(_rows(), "temporal", seed=42)
    assert selection["TransactionDT"].max() < promotion["TransactionDT"].min()
    assert set(selection["TransactionID"]).isdisjoint(promotion["TransactionID"])


def test_same_rows_with_different_artifact_bytes_have_same_semantic_identity(tmp_path):
    first = _write(_rows(), tmp_path / "first")
    second = _write(_rows(), tmp_path / "second")
    second_path = tmp_path / "second" / DATA_FILE
    _rows().to_parquet(second_path, index=False, compression=None)
    second["artifact_sha256"] = _file_sha256(second_path)

    assert first["artifact_sha256"] != second["artifact_sha256"]
    assert first["fingerprint"] == second["fingerprint"]
    assert semantic_evidence_identity(first) == semantic_evidence_identity(second)
    pd.testing.assert_frame_equal(
        load_and_verify_evaluation(tmp_path / "first" / DATA_FILE, first),
        load_and_verify_evaluation(second_path, second),
    )


def test_changed_row_value_changes_semantic_fingerprint_and_blocks_comparison(
    tmp_path, monkeypatch
):
    first = _write(_rows(), tmp_path / "first")
    changed = _rows()
    changed.loc[0, "TransactionAmt"] = 999.0
    second = _write(changed, tmp_path / "second")
    assert first["fingerprint"] != second["fingerprint"]
    report = _compare_manifests(monkeypatch, second, first)
    assert report["decision"] == "blocked"
    assert "evidence does not match" in report["reason"]


def test_changed_row_order_and_identity_block_comparison(tmp_path, monkeypatch):
    first = _write(_rows(), tmp_path / "first")
    reordered = _rows().iloc[::-1].reset_index(drop=True)
    second = _write(reordered, tmp_path / "second")

    assert first["row_identity_fingerprint"] != second["row_identity_fingerprint"]
    assert first["fingerprint"] != second["fingerprint"]
    report = _compare_manifests(monkeypatch, second, first)
    assert report["decision"] == "blocked"
    assert "evidence does not match" in report["reason"]


def test_missing_fingerprint_fails_closed(tmp_path):
    manifest = _write(_rows(), tmp_path)
    manifest.pop("fingerprint")
    with pytest.raises(PromotionEvaluationError, match="missing fields"):
        semantic_evidence_identity(manifest)


def test_missing_source_fingerprint_fails_closed(tmp_path):
    manifest = _write(_rows(), tmp_path)
    manifest["source_data_fingerprint"] = {"source": None}
    with pytest.raises(PromotionEvaluationError, match="source data fingerprint"):
        semantic_evidence_identity(manifest)


def test_tampered_artifact_fails_fingerprint_verification(tmp_path):
    manifest = _write(_rows(), tmp_path / "original")
    tampered = tmp_path / "tampered.parquet"
    shutil.copyfile(tmp_path / "original" / DATA_FILE, tampered)
    with tampered.open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(PromotionEvaluationError, match="artifact checksum mismatch"):
        load_and_verify_evaluation(tampered, manifest)


class _Wrapper:
    def __init__(self, probabilities, threshold=0.5):
        self.probabilities = np.asarray(probabilities)
        self.threshold = threshold

    def predict(self, _context, model_input):
        return pd.DataFrame(
            {
                "fraud_probability": self.probabilities,
                "fraud_prediction": (self.probabilities >= self.threshold).astype(int),
                "threshold": self.threshold,
            }
        )


def _metadata(threshold=0.5):
    return {
        "threshold": threshold,
        "cost": {"false_negative_cost": 25.0, "false_positive_cost": 1.0},
    }


def test_rescoring_uses_stored_threshold_and_expected_cost():
    rows = _rows().iloc[:4]
    metrics = _score_model(_Wrapper([0.1, 0.4, 0.6, 0.9]), _metadata(), rows, "1")
    assert metrics["threshold"] == 0.5
    assert metrics["confusion_matrix"] == {"tn": 1, "fp": 1, "fn": 1, "tp": 1}
    assert metrics["expected_cost"] == 26.0
    assert metrics["expected_cost_per_sample"] == 6.5


@pytest.mark.parametrize("bad_probability", [np.nan, np.inf, -0.1, 1.1])
def test_invalid_prediction_output_fails_closed(bad_probability):
    rows = _rows().iloc[:4]
    with pytest.raises(PromotionComparisonError, match="out-of-range probabilities"):
        _score_model(_Wrapper([0.1, bad_probability, 0.6, 0.9]), _metadata(), rows, "1")


def test_prediction_threshold_must_match_stored_threshold():
    rows = _rows().iloc[:4]
    with pytest.raises(PromotionComparisonError, match="stored threshold"):
        _score_model(_Wrapper([0.1, 0.4, 0.6, 0.9], threshold=0.4), _metadata(0.5), rows, "1")


def test_missing_computed_metric_fails_closed(monkeypatch):
    rows = _rows().iloc[:4]

    def incomplete_metrics(*_args, **_kwargs):
        return {"roc_auc": 0.5}

    monkeypatch.setattr("src.registry.compare.compute_metrics", incomplete_metrics)
    with pytest.raises(PromotionComparisonError, match="missing or non-finite metric"):
        _score_model(_Wrapper([0.1, 0.4, 0.6, 0.9]), _metadata(), rows, "1")
