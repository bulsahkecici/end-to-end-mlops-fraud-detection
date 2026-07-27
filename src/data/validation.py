"""Lightweight structural validation for raw IEEE-CIS transaction data.

This is not a full data-quality framework — it exists to fail fast and
loudly when the input CSVs don't look like what the rest of the pipeline
expects, instead of letting a malformed file silently propagate into
training or inference.
"""
from __future__ import annotations

import pandas as pd

REQUIRED_TRANSACTION_COLUMNS = {"TransactionID", "TransactionDT", "TransactionAmt"}
TARGET_COL = "isFraud"


class DataValidationError(ValueError):
    """Raised when raw input data fails structural validation."""


def validate_raw_transactions(df: pd.DataFrame, require_target: bool = False) -> None:
    """Validate a raw (pre-feature-engineering) transaction dataframe.

    Checks column presence, target domain, id uniqueness and basic sanity
    of the time column. Raises :class:`DataValidationError` on failure.
    """
    missing = REQUIRED_TRANSACTION_COLUMNS - set(df.columns)
    if missing:
        raise DataValidationError(f"Missing required columns: {sorted(missing)}")

    if require_target:
        if TARGET_COL not in df.columns:
            raise DataValidationError(f"Missing required target column: {TARGET_COL!r}")
        bad_values = set(df[TARGET_COL].dropna().unique()) - {0, 1}
        if bad_values:
            raise DataValidationError(
                f"{TARGET_COL} must be binary (0/1), found values: {sorted(bad_values)}"
            )
        if df[TARGET_COL].isna().any():
            raise DataValidationError(f"{TARGET_COL} contains missing values")

    if df["TransactionID"].duplicated().any():
        n_dup = int(df["TransactionID"].duplicated().sum())
        raise DataValidationError(f"TransactionID has {n_dup} duplicate values")

    if (df["TransactionDT"] < 0).any():
        raise DataValidationError("TransactionDT contains negative values")

    if len(df) == 0:
        raise DataValidationError("Dataframe is empty")


def summarize_split(df: pd.DataFrame, name: str) -> dict:
    """Return a small, loggable summary of a train/val/test split."""
    summary = {
        "name": name,
        "n_rows": int(len(df)),
    }
    if "TransactionDT" in df.columns and len(df) > 0:
        summary["dt_min"] = float(df["TransactionDT"].min())
        summary["dt_max"] = float(df["TransactionDT"].max())
    if TARGET_COL in df.columns and len(df) > 0:
        n_fraud = int(df[TARGET_COL].sum())
        summary["n_fraud"] = n_fraud
        summary["fraud_rate"] = float(n_fraud / len(df))
    return summary
