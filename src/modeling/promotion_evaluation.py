"""Frozen evaluation artifacts used exclusively by the promotion gate.

The artifact contains raw, labelled rows.  Promotion loads each registered
model's own fitted pipeline and scores both models against these exact rows;
the final test split is never written here or read by registry code.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

from src.data.validation import summarize_split

TARGET_COL = "isFraud"
ROW_ID_COL = "TransactionID"
TIME_COL = "TransactionDT"
ARTIFACT_DIR = "promotion_evaluation"
DATA_FILE = "rows.parquet"
MANIFEST_FILE = "manifest.json"
FORMAT_VERSION = 1
FINGERPRINT_ALGORITHM = "sha256-canonical-json-v1"
PROMOTION_SHARE_OF_VALIDATION_POOL = 0.5


class PromotionEvaluationError(RuntimeError):
    """Raised when canonical promotion evidence is absent or invalid."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_json_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _canonical_scalar(value: Any) -> list[Any]:
    """Encode a scalar without lossy float formatting or null/string collisions."""
    if value is None or (not isinstance(value, list | dict) and bool(pd.isna(value))):
        return ["null", None]
    if isinstance(value, bool | np.bool_):
        return ["bool", bool(value)]
    if isinstance(value, int | np.integer):
        return ["integer", int(value)]
    if isinstance(value, float | np.floating):
        return ["float", float(value).hex()]
    if isinstance(value, pd.Timestamp | np.datetime64):
        return ["datetime", pd.Timestamp(value).isoformat()]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    return ["string", str(value)]


def _fingerprint_rows(rows: pd.DataFrame) -> str:
    """Hash normalized columns and ordered cell values, independent of Parquet bytes."""
    digest = hashlib.sha256()
    header = json.dumps(list(rows.columns), ensure_ascii=True, separators=(",", ":"))
    digest.update(header.encode())
    digest.update(b"\n")
    for row in rows.itertuples(index=False, name=None):
        normalized = [_canonical_scalar(value) for value in row]
        payload = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
        digest.update(payload.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def split_selection_and_promotion(
    validation_pool: pd.DataFrame,
    strategy: str,
    seed: int,
    promotion_share: float = PROMOTION_SHARE_OF_VALIDATION_POOL,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split development rows into selection-validation and promotion rows.

    Temporal data keeps the later half for promotion.  The random fallback is
    stratified when the target has enough examples.  Neither branch touches
    the already-separated final test split.
    """
    if not 0.0 < promotion_share < 1.0:
        raise ValueError("promotion_share must be strictly between 0 and 1")
    if len(validation_pool) < 2:
        raise ValueError("validation pool needs at least two rows")

    if strategy == "temporal":
        ordered = validation_pool.sort_values(TIME_COL, kind="mergesort")
        cut = max(1, min(len(ordered) - 1, int(round(len(ordered) * (1 - promotion_share)))))
        return ordered.iloc[:cut].copy(), ordered.iloc[cut:].copy()
    if strategy == "random":
        target = validation_pool[TARGET_COL]
        counts = target.value_counts()
        stratify = target if len(counts) > 1 and int(counts.min()) >= 2 else None
        selection, promotion = train_test_split(
            validation_pool,
            test_size=promotion_share,
            random_state=seed,
            stratify=stratify,
        )
        return selection.copy(), promotion.copy()
    raise ValueError(f"Unknown split_strategy={strategy!r}, expected 'temporal' or 'random'")


def _observed_identity(df: pd.DataFrame) -> dict[str, Any]:
    if TARGET_COL not in df or ROW_ID_COL not in df:
        raise PromotionEvaluationError(
            f"promotion rows must contain {ROW_ID_COL!r} and {TARGET_COL!r}"
        )
    if df.empty:
        raise PromotionEvaluationError("promotion evaluation dataset is empty")
    if df[ROW_ID_COL].duplicated().any():
        raise PromotionEvaluationError("promotion evaluation row identifiers are not unique")

    target = pd.to_numeric(df[TARGET_COL], errors="coerce")
    if target.isna().any() or not set(target.unique()).issubset({0, 1}):
        raise PromotionEvaluationError("promotion evaluation target must contain only 0/1")
    distribution = {str(label): int((target == label).sum()) for label in (0, 1)}
    boundaries = summarize_split(df, "promotion_evaluation")
    row_ids = [str(value) for value in df[ROW_ID_COL].tolist()]
    return {
        "row_count": int(len(df)),
        "target_distribution": distribution,
        "time_boundaries": {
            key: boundaries.get(key) for key in ("dt_min", "dt_max") if key in boundaries
        },
        "row_identity_fingerprint": _stable_json_hash(row_ids),
        "columns": list(df.columns),
    }


def write_evaluation_artifact(
    rows: pd.DataFrame,
    output_dir: Path,
    *,
    data_source: str,
    split_strategy: str,
    source_data_fingerprint: dict[str, Any],
    random_seed: int,
) -> dict[str, Any]:
    """Materialize exact promotion rows and return their immutable manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    canonical = rows.reset_index(drop=True)
    identity = _observed_identity(canonical)
    data_path = output_dir / DATA_FILE
    canonical.to_parquet(data_path, index=False)
    fingerprint = _fingerprint_rows(canonical)
    artifact_sha256 = _sha256_file(data_path)
    dataset_id = f"{data_source}:{split_strategy}:promotion-evaluation"
    manifest: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "purpose": "promotion_evaluation",
        "derived_from": "validation_pool",
        "excludes_final_test": True,
        "dataset_id": dataset_id,
        "dataset_version": fingerprint,
        "fingerprint": fingerprint,
        "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
        "artifact_sha256": artifact_sha256,
        "artifact_path": f"{ARTIFACT_DIR}/{DATA_FILE}",
        "data_source": data_source,
        "source_data_fingerprint": source_data_fingerprint,
        "split_strategy": split_strategy,
        "random_seed": random_seed,
        **identity,
    }
    (output_dir / MANIFEST_FILE).write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n"
    )
    return manifest


def evidence_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return every manifest field that must match across model versions."""
    required = (
        "format_version",
        "purpose",
        "derived_from",
        "excludes_final_test",
        "dataset_id",
        "dataset_version",
        "fingerprint",
        "fingerprint_algorithm",
        "artifact_sha256",
        "row_count",
        "target_distribution",
        "time_boundaries",
        "row_identity_fingerprint",
        "columns",
        "source_data_fingerprint",
        "split_strategy",
    )
    missing = [key for key in required if key not in manifest]
    if missing:
        raise PromotionEvaluationError(f"promotion manifest missing fields: {missing}")
    if manifest["purpose"] != "promotion_evaluation" or not manifest["excludes_final_test"]:
        raise PromotionEvaluationError("manifest does not identify a non-test promotion dataset")
    fingerprint = manifest["fingerprint"]
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint)
        or manifest["dataset_version"] != fingerprint
    ):
        raise PromotionEvaluationError("manifest has an invalid evaluation fingerprint/version")
    if manifest["fingerprint_algorithm"] != FINGERPRINT_ALGORITHM:
        raise PromotionEvaluationError("manifest uses an unsupported fingerprint algorithm")
    artifact_sha256 = manifest["artifact_sha256"]
    if (
        not isinstance(artifact_sha256, str)
        or len(artifact_sha256) != 64
        or any(character not in "0123456789abcdef" for character in artifact_sha256)
    ):
        raise PromotionEvaluationError("manifest has an invalid artifact checksum")
    source_fingerprint = manifest["source_data_fingerprint"]
    if not isinstance(source_fingerprint, dict) or not source_fingerprint:
        raise PromotionEvaluationError("manifest has no source data fingerprint")
    if any(value is None or value == "" for value in source_fingerprint.values()):
        raise PromotionEvaluationError("manifest source data fingerprint is incomplete")
    if not isinstance(manifest["row_count"], int) or manifest["row_count"] <= 0:
        raise PromotionEvaluationError("manifest row count is invalid")
    return {key: manifest[key] for key in required}


def load_and_verify_evaluation(data_path: Path, manifest: dict[str, Any]) -> pd.DataFrame:
    """Load an artifact only after its bytes and declared identity agree."""
    evidence_identity(manifest)
    if not data_path.is_file():
        raise PromotionEvaluationError(f"promotion evaluation artifact missing: {data_path}")
    actual_artifact_sha256 = _sha256_file(data_path)
    if actual_artifact_sha256 != manifest["artifact_sha256"]:
        raise PromotionEvaluationError(
            "promotion evaluation artifact checksum mismatch: "
            f"expected {manifest['artifact_sha256']}, got {actual_artifact_sha256}"
        )
    try:
        rows = pd.read_parquet(data_path)
    except Exception as exc:
        raise PromotionEvaluationError(
            f"could not read promotion evaluation artifact: {exc}"
        ) from exc
    observed = _observed_identity(rows)
    actual_fingerprint = _fingerprint_rows(rows)
    if actual_fingerprint != manifest["fingerprint"]:
        raise PromotionEvaluationError(
            "promotion evaluation canonical fingerprint mismatch: "
            f"expected {manifest['fingerprint']}, got {actual_fingerprint}"
        )
    for key, value in observed.items():
        if manifest.get(key) != value:
            raise PromotionEvaluationError(
                f"promotion evaluation manifest mismatch for {key}: "
                f"expected {manifest.get(key)!r}, got {value!r}"
            )
    if not math.isfinite(float(rows[TARGET_COL].mean())):
        raise PromotionEvaluationError("promotion evaluation target distribution is invalid")
    return rows
