"""Small reproducible experiment records for model-quality comparisons."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd

FINGERPRINT_ALGORITHM = "sha256-canonical-json-v1"


def _canonical_scalar(value: Any) -> list[Any]:
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
    return ["string", str(value)]


def fingerprint_rows(rows: pd.DataFrame) -> str:
    """Return a stable fingerprint of ordered columns and row values."""
    digest = hashlib.sha256()
    digest.update(json.dumps(list(rows.columns), separators=(",", ":")).encode())
    digest.update(b"\n")
    for row in rows.itertuples(index=False, name=None):
        normalized = [_canonical_scalar(value) for value in row]
        digest.update(json.dumps(normalized, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def build_experiment_record(
    *,
    variant_id: str,
    categorical_strategy: str,
    calibration_strategy: str,
    model_params: dict[str, Any],
    random_seed: int,
    split_strategy: str,
    calibration_fit_rows: pd.DataFrame,
    selection_rows: pd.DataFrame,
    source_data_fingerprint: dict[str, Any],
) -> dict[str, Any]:
    """Describe one variant and the exact selection evidence it used."""
    selection_fingerprint = fingerprint_rows(selection_rows.reset_index(drop=True))
    calibration_fingerprint = fingerprint_rows(calibration_fit_rows.reset_index(drop=True))
    return {
        "variant_id": variant_id,
        "feature_strategy": "train_fitted_sklearn_pipeline",
        "categorical_strategy": categorical_strategy,
        "calibration_strategy": calibration_strategy,
        "model_params": model_params,
        "random_seed": random_seed,
        "split_strategy": split_strategy,
        "calibration_fit": {
            "purpose": "early_stopping_and_probability_calibration_fit",
            "excludes_promotion_evaluation": True,
            "excludes_final_test": True,
            "fingerprint": calibration_fingerprint,
            "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
            "row_count": int(len(calibration_fit_rows)),
        },
        "selection_evaluation": {
            "purpose": "model_selection_validation",
            "excludes_promotion_evaluation": True,
            "excludes_final_test": True,
            "dataset_id": f"selection-validation:{split_strategy}",
            "dataset_version": selection_fingerprint,
            "fingerprint": selection_fingerprint,
            "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
            "row_count": int(len(selection_rows)),
            "source_data_fingerprint": source_data_fingerprint,
        },
    }
