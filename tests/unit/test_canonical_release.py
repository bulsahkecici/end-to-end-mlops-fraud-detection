"""Safe release metadata must reject corrupt inputs and preserve identity."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts.canonical_release import audit_file, semantic_hash, write_json


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
