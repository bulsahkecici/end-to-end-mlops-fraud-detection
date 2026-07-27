"""Minimal data/prediction drift check: compares a reference dataset
(training data) against a current batch (e.g. recent inference logs or a
fresh sample) on simple, dependency-light summary statistics.

Deliberately does not pull in a full drift-detection framework (e.g.
Evidently) — for a project this size that would be a heavyweight
dependency for a check that population-stability-index-style summary
statistics already cover reasonably well.

Usage:
    python -m src.monitoring.drift --reference path/to/reference.csv --current path/to/current.csv
    python -m src.monitoring.drift --synthetic   # self-contained demo using synthetic data
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import settings
from src.data.ingest import make_synthetic_transactions
from src.features.pipeline import infer_schema

logger = logging.getLogger(__name__)

# A numeric-distribution shift larger than this (in standardized mean units)
# or a missing-rate shift larger than this (absolute) is flagged.
NUMERIC_MEAN_SHIFT_THRESHOLD = 0.5
MISSING_RATE_SHIFT_THRESHOLD = 0.10
CATEGORY_SHIFT_THRESHOLD = 0.10  # max absolute frequency-share change per category


def _numeric_drift(reference: pd.Series, current: pd.Series) -> dict:
    ref_mean, ref_std = reference.mean(), reference.std(ddof=0) or 1e-9
    cur_mean = current.mean()
    standardized_shift = float(abs(cur_mean - ref_mean) / ref_std)
    ref_missing = float(reference.isna().mean())
    cur_missing = float(current.isna().mean())
    return {
        "type": "numeric",
        "reference_mean": float(ref_mean),
        "current_mean": float(cur_mean) if not np.isnan(cur_mean) else None,
        "reference_missing_rate": ref_missing,
        "current_missing_rate": cur_missing,
        "standardized_mean_shift": standardized_shift,
        "missing_rate_shift": abs(cur_missing - ref_missing),
        "drifted": bool(
            standardized_shift > NUMERIC_MEAN_SHIFT_THRESHOLD
            or abs(cur_missing - ref_missing) > MISSING_RATE_SHIFT_THRESHOLD
        ),
    }


def _categorical_drift(reference: pd.Series, current: pd.Series) -> dict:
    ref_freq = reference.value_counts(normalize=True, dropna=True)
    cur_freq = current.value_counts(normalize=True, dropna=True)
    all_categories = set(ref_freq.index) | set(cur_freq.index)
    max_shift = 0.0
    per_category = {}
    for cat in all_categories:
        r, c = float(ref_freq.get(cat, 0.0)), float(cur_freq.get(cat, 0.0))
        shift = abs(r - c)
        per_category[str(cat)] = {"reference_share": r, "current_share": c, "shift": shift}
        max_shift = max(max_shift, shift)
    return {
        "type": "categorical",
        "max_category_shift": max_shift,
        "new_categories": sorted(str(c) for c in (set(cur_freq.index) - set(ref_freq.index))),
        "per_category": per_category,
        "drifted": bool(max_shift > CATEGORY_SHIFT_THRESHOLD),
    }


def compute_drift_report(reference: pd.DataFrame, current: pd.DataFrame) -> dict:
    """Compare `current` against `reference` column by column."""
    schema = infer_schema(reference.drop(columns=["isFraud"], errors="ignore"))
    columns_report: dict[str, dict] = {}

    for col in schema.numeric_cols:
        if col in current.columns:
            columns_report[col] = _numeric_drift(reference[col], current[col])
    for col in schema.categorical_cols:
        if col in current.columns:
            columns_report[col] = _categorical_drift(reference[col], current[col])

    prediction_drift = None
    if "isFraud" in reference.columns and "isFraud" in current.columns:
        prediction_drift = {
            "reference_fraud_rate": float(reference["isFraud"].mean()),
            "current_fraud_rate": float(current["isFraud"].mean()),
        }
        prediction_drift["fraud_rate_shift"] = abs(
            prediction_drift["reference_fraud_rate"] - prediction_drift["current_fraud_rate"]
        )

    n_drifted = sum(1 for c in columns_report.values() if c["drifted"])
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "reference_rows": int(len(reference)),
        "current_rows": int(len(current)),
        "n_columns_checked": len(columns_report),
        "n_columns_drifted": n_drifted,
        "overall_drift_detected": bool(n_drifted > 0),
        "columns": columns_report,
        "prediction_drift": prediction_drift,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Drift report",
        "",
        f"Generated at: {report['generated_at']}",
        f"Reference rows: {report['reference_rows']} | Current rows: {report['current_rows']}",
        "",
        f"**Overall drift detected:** {report['overall_drift_detected']} "
        f"({report['n_columns_drifted']}/{report['n_columns_checked']} columns flagged)",
        "",
        "| column | type | drifted | detail |",
        "|---|---|---|---|",
    ]
    for col, info in report["columns"].items():
        if info["type"] == "numeric":
            detail = f"mean shift={info['standardized_mean_shift']:.3f}"
        else:
            detail = f"max category shift={info['max_category_shift']:.3f}"
        lines.append(f"| {col} | {info['type']} | {info['drifted']} | {detail} |")
    if report.get("prediction_drift"):
        pd_info = report["prediction_drift"]
        lines.append("")
        lines.append(
            f"Fraud rate: reference={pd_info['reference_fraud_rate']:.4f}, "
            f"current={pd_info['current_fraud_rate']:.4f}, shift={pd_info['fraud_rate_shift']:.4f}"
        )
    return "\n".join(lines) + "\n"


def save_reports(report: dict) -> tuple[Path, Path]:
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = settings.reports_dir / "drift_report.json"
    md_path = settings.reports_dir / "drift_report.md"
    json_path.write_text(json.dumps(report, indent=2, default=str))
    md_path.write_text(render_markdown(report))
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare a current data batch against a reference."
    )
    parser.add_argument(
        "--reference", type=Path, default=None, help="Reference CSV (e.g. training data)"
    )
    parser.add_argument("--current", type=Path, default=None, help="Current CSV to check for drift")
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Self-contained demo: compares two synthetic samples with an injected shift",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if args.synthetic or not (args.reference and args.current):
        reference = make_synthetic_transactions(n=2000, seed=1)
        current = make_synthetic_transactions(n=500, seed=2, fraud_rate=0.15)
        current["TransactionAmt"] = current["TransactionAmt"] * 1.8  # inject a shift
    else:
        reference = pd.read_csv(args.reference)
        current = pd.read_csv(args.current)

    report = compute_drift_report(reference, current)
    json_path, md_path = save_reports(report)
    logger.info("Wrote %s and %s", json_path, md_path)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
