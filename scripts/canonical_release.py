"""Audit restricted IEEE-CIS files and freeze safe canonical release metadata.

Run with ``python -m scripts.canonical_release``. No rows are exported.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from src.config import settings
from src.data.ingest import load_train
from src.modeling.calibration import split_calibration_and_selection
from src.modeling.experiment import FINGERPRINT_ALGORITHM, fingerprint_rows
from src.modeling.promotion_evaluation import split_selection_and_promotion
from src.modeling.train import default_lgbm_params
from src.modeling.validation import temporal_split
from src.utils.repro import fingerprint_file_contents, get_env_info, get_git_info

RELEASE = Path("releases/ieee-cis-v1")


def semantic_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def audit_file(path: Path) -> tuple[dict, set[int]]:
    """Bounded-memory structural audit; retain IDs locally only for join checks."""
    ids: set[int] = set()
    missing: dict[str, int] = {}
    count = duplicates = positives = 0
    dt_min = dt_max = None
    columns = list(pd.read_csv(path, nrows=0).columns)
    labelled = path.name == "train_transaction.csv"
    if ("isFraud" in columns) != labelled:
        raise ValueError(f"Unexpected target presence in {path.name}")
    for chunk in pd.read_csv(path, chunksize=25000):
        if chunk.TransactionID.isna().any():
            raise ValueError("Missing TransactionID")
        chunk_ids = set(chunk.TransactionID.astype(int))
        duplicates += len(chunk) - len(chunk_ids) + len(ids & chunk_ids)
        ids.update(chunk_ids)
        count += len(chunk)
        for column, n_missing in chunk.isna().sum().items():
            missing[str(column)] = missing.get(str(column), 0) + int(n_missing)
        if "TransactionDT" in columns:
            if chunk.TransactionDT.isna().any() or (chunk.TransactionDT < 0).any():
                raise ValueError("Invalid TransactionDT")
            low, high = int(chunk.TransactionDT.min()), int(chunk.TransactionDT.max())
            dt_min = low if dt_min is None else min(dt_min, low)
            dt_max = high if dt_max is None else max(dt_max, high)
        if labelled:
            if not chunk.isFraud.isin([0, 1]).all():
                raise ValueError("Invalid target")
            positives += int(chunk.isFraud.sum())
    if duplicates or not count:
        raise ValueError(f"Duplicate IDs or empty file: {path.name}")
    result = {
        "bytes": path.stat().st_size,
        "sha256": fingerprint_file_contents(path),
        "rows": count,
        "columns": columns,
        "duplicate_transaction_ids": duplicates,
        "missing_counts": missing,
    }
    if dt_min is not None:
        result["TransactionDT"] = {"min": dt_min, "max": dt_max}
    if labelled:
        result["target"] = {"positive": positives, "negative": count - positives}
    return result, ids


def main() -> None:
    if (RELEASE / "canonical_config.json").exists():
        raise RuntimeError("Preregistration already exists; refusing to overwrite it")
    files = {}
    identity = {}
    for partition in ("train", "test"):
        transaction = f"{partition}_transaction.csv"
        identities = f"{partition}_identity.csv"
        files[transaction], transaction_ids = audit_file(settings.ieee_data_dir / transaction)
        files[identities], identity_ids = audit_file(settings.ieee_data_dir / identities)
        if identity_ids - transaction_ids:
            raise ValueError("Identity rows without matching transactions")
        identity[partition] = {
            "matched_identity_rows": len(identity_ids & transaction_ids),
            "transaction_rows": len(transaction_ids),
            "coverage": len(identity_ids) / len(transaction_ids),
        }
        print(f"Audited {partition} source files", flush=True)
    source = {name: entry["sha256"] for name, entry in files.items()}
    dataset = {
        "release_id": "ieee-cis-v1",
        "data_source": "real_ieee_cis",
        "files": files,
        "identity_join": identity,
        "source_fingerprint": semantic_hash(source),
        "training_source_fingerprint": {k: v for k, v in source.items() if k.startswith("train_")},
        "competition_test_is_unlabelled_and_not_used": True,
    }
    settings.sample_rows = None
    rows = load_train()
    train, development, final = temporal_split(rows, 0.7, 0.15, 0.15)
    selection_pool, promotion = split_selection_and_promotion(development, "temporal", 42)
    calibration, selection = split_calibration_and_selection(selection_pool, "temporal", 42)
    splits = {}
    previous_max = None
    for name, frame in (
        ("train", train),
        ("calibration_fit", calibration),
        ("selection_validation", selection),
        ("promotion_evaluation", promotion),
        ("final_test", final),
    ):
        low, high = int(frame.TransactionDT.min()), int(frame.TransactionDT.max())
        if previous_max is not None and low < previous_max:
            raise ValueError("Temporal partition overlap")
        previous_max = high
        splits[name] = {
            "rows": len(frame),
            "dt_min": low,
            "dt_max": high,
            "fingerprint": fingerprint_rows(frame.reset_index(drop=True)),
            "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
        }
        print(f"Frozen {name} identity", flush=True)
    config = {
        "release_id": "ieee-cis-v1",
        "source_commit": get_git_info(settings.project_root)["commit"],
        "data_source": "ieee",
        "sample_rows": None,
        "sampling_strategy": "time_ordered",
        "source_fingerprint": dataset["source_fingerprint"],
        "split_strategy": "temporal",
        "split_ratios": {"train": 0.7, "development": 0.15, "final_test": 0.15},
        "development_promotion_share": 0.5,
        "selection_pool_calibration_share": 0.5,
        "categorical_strategy": "ordinal_drop_high_cardinality",
        "cat_nunique_max": 200,
        "calibration_strategy": "none",
        "model_params": default_lgbm_params(42),
        "early_stopping_rounds": 20,
        "early_stopping_metric": "auc",
        "random_seed": 42,
        "threshold_strategy": "best_f1",
        "cost": {"false_negative": 25.0, "false_positive": 1.0},
        "model_name": "ieee_fraud_lgbm",
        "experiment_name": "ieee-cis-v1",
        "variant_id": "ieee-cis-v1-ordinal-none",
        "promotion_policy": {"min_pr_auc": 0.1, "min_recall": 0.1, "max_regression": 0.02},
        "tracking": "isolated SQLite with local artifacts under artifacts/releases/ieee-cis-v1",
        "environment": get_env_info(),
        "splits_fingerprint": semantic_hash(splits),
        "full_data_rationale": (
            "36 GiB host RAM; optimized transaction dtypes; 590540 labelled rows"
        ),
        "final_test_policy": (
            "Reporting once after frozen immutable promotion and deployment; no selection"
        ),
    }
    write_json(RELEASE / "dataset_manifest.json", dataset)
    write_json(RELEASE / "split_manifest.json", splits)
    write_json(RELEASE / "canonical_config.json", config)
    write_json(
        RELEASE / "preregistration.json",
        {
            "config_sha256": semantic_hash(config),
            "dataset_sha256": semantic_hash(dataset),
            "splits_sha256": semantic_hash(splits),
            "final_test_metrics_inspected": False,
        },
    )


if __name__ == "__main__":
    main()
