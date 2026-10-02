"""Safe release metadata must reject corrupt inputs and preserve identity."""

from __future__ import annotations

import json
import shutil
import sys

import pandas as pd
import pytest

from scripts.canonical_release import audit_file, semantic_hash, write_json
from scripts.verify_canonical_release import verify_release
from src.config import PROJECT_ROOT


def test_audit_has_only_metadata_and_detects_duplicate_ids(tmp_path):
    path = tmp_path / "train_transaction.csv"
    rows = pd.DataFrame(
        {
            "TransactionID": [1, 2],
            "TransactionDT": [10, 20],
            "TransactionAmt": [99.5, 200.0],
            "isFraud": [0, 1],
        }
    )
    rows.to_csv(path, index=False)
    manifest, ids = audit_file(path)
    assert ids == {1, 2}
    assert manifest["rows"] == 2
    assert manifest["target"] == {"positive": 1, "negative": 1}
    assert "99.5" not in json.dumps(manifest)
    assert len(manifest["sha256"]) == 64
    rows.loc[1, "TransactionID"] = 1
    rows.to_csv(path, index=False)
    with pytest.raises(ValueError, match="Duplicate IDs"):
        audit_file(path)


def test_audit_rejects_target_in_competition_test(tmp_path):
    path = tmp_path / "test_transaction.csv"
    pd.DataFrame({"TransactionID": [1], "isFraud": [0]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="target presence"):
        audit_file(path)


def test_semantic_identity_and_strict_json(tmp_path):
    assert semantic_hash({"a": 1, "b": 2}) == semantic_hash({"b": 2, "a": 1})
    assert semantic_hash({"a": 1}) != semantic_hash({"a": 2})
    with pytest.raises(ValueError):
        write_json(tmp_path / "bad.json", {"metric": float("nan")})
    path = tmp_path / "safe.json"
    write_json(path, {"b": 2, "a": 1})
    first = path.read_bytes()
    write_json(path, {"a": 1, "b": 2})
    assert first == path.read_bytes()


def test_published_release_chain_verifies():
    result = verify_release(PROJECT_ROOT / "releases/ieee-cis-v1")
    assert result["verified"] is True
    assert result["model_uri"] == "models:/ieee_fraud_lgbm/1"


@pytest.mark.parametrize(
    ("filename", "field", "replacement"),
    [
        ("canonical_config", "calibration_strategy", "sigmoid"),
        ("deployment_manifest", "model_version", "2"),
        ("final_test_metrics", "model_uri", "models:/ieee_fraud_lgbm@champion"),
        ("training_manifest", "run_id", "wrong-run"),
        ("model_metadata", "threshold", 0.5),
    ],
)
def test_release_chain_rejects_identity_tampering(tmp_path, filename, field, replacement):
    directory = tmp_path / "release"
    shutil.copytree(PROJECT_ROOT / "releases/ieee-cis-v1", directory)
    path = directory / f"{filename}.json"
    content = json.loads(path.read_text())
    content[field] = replacement
    write_json(path, content)
    with pytest.raises(ValueError):
        verify_release(directory)


def test_replay_rejects_changed_source_before_training(tmp_path, monkeypatch):
    from scripts import run_canonical_training

    monkeypatch.setattr(sys, "argv", ["replay", "--output-directory", str(tmp_path)])
    monkeypatch.setattr(run_canonical_training, "fingerprint_file_contents", lambda _: "wrong")
    monkeypatch.setattr(
        run_canonical_training,
        "run_training",
        lambda **_: pytest.fail("Training must not run on mismatched source files"),
    )
    with pytest.raises(ValueError, match="Source hash mismatch"):
        run_canonical_training.main()
