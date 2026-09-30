"""Rescore candidate and champion on one frozen promotion dataset."""

from __future__ import annotations

import json
import logging
import math
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import pandas as pd
from mlflow.exceptions import MlflowException

from src.config import settings
from src.modeling.evaluate import compute_metrics
from src.modeling.promotion_evaluation import (
    TARGET_COL,
    PromotionEvaluationError,
    load_and_verify_evaluation,
    semantic_evidence_identity,
)
from src.modeling.threshold import expected_cost

logger = logging.getLogger(__name__)

COMPARED_METRICS = [
    "roc_auc",
    "pr_auc",
    "precision",
    "recall",
    "f1",
    "brier_score",
    "log_loss",
    "expected_cost",
    "expected_cost_per_sample",
]


class RegistryLookupError(RuntimeError):
    """Registry access failed for a reason other than an absent alias."""


class PromotionComparisonError(RuntimeError):
    """Comparable, trustworthy promotion metrics could not be produced."""


def get_alias_model_version(client: Any, model_name: str, alias: str) -> Any | None:
    """Return ``None`` only for MLflow's exact missing-alias response."""
    try:
        return client.get_model_version_by_alias(model_name, alias)
    except MlflowException as exc:
        expected = f"Registered model alias {alias} not found."
        if exc.error_code == "INVALID_PARAMETER_VALUE" and str(exc) == expected:
            return None
        raise RegistryLookupError(
            f"could not resolve {model_name!r} alias {alias!r}: {exc}"
        ) from exc
    except Exception as exc:
        raise RegistryLookupError(
            f"could not resolve {model_name!r} alias {alias!r}: {exc}"
        ) from exc


def _version_ref(mv: Any, alias: str) -> dict[str, Any]:
    return {"alias": alias, "version": str(mv.version), "run_id": str(mv.run_id)}


def _load_version(model_name: str, version: str) -> tuple[Any, Any, dict[str, Any]]:
    uri = f"models:/{model_name}/{version}"
    try:
        pyfunc_model = mlflow.pyfunc.load_model(uri)
        wrapper = pyfunc_model.unwrap_python_model()
        metadata = wrapper.metadata
    except Exception as exc:
        raise PromotionComparisonError(f"could not load immutable model {uri}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise PromotionComparisonError(f"model {uri} has no valid metadata")
    return pyfunc_model, wrapper, metadata


def _manifest(metadata: dict[str, Any], version: str) -> dict[str, Any]:
    manifest = metadata.get("promotion_evaluation")
    if not isinstance(manifest, dict):
        raise PromotionComparisonError(
            f"model version {version} is missing promotion evaluation evidence"
        )
    try:
        semantic_evidence_identity(manifest)
    except PromotionEvaluationError as exc:
        raise PromotionComparisonError(f"model version {version}: {exc}") from exc
    return manifest


def _download_and_verify(
    client: Any, run_id: str, manifest: dict[str, Any], destination: Path
) -> pd.DataFrame:
    artifact_path = manifest.get("artifact_path")
    if not isinstance(artifact_path, str) or not artifact_path:
        raise PromotionComparisonError("promotion manifest has no artifact_path")
    try:
        destination.mkdir(parents=True, exist_ok=True)
        local_path = Path(client.download_artifacts(run_id, artifact_path, str(destination)))
        return load_and_verify_evaluation(local_path, manifest)
    except (MlflowException, OSError, PromotionEvaluationError, ValueError) as exc:
        raise PromotionComparisonError(
            f"could not verify promotion evaluation artifact for run {run_id}: {exc}"
        ) from exc


def _cost_settings(metadata: dict[str, Any], version: str) -> tuple[float, float]:
    cost = metadata.get("cost")
    if not isinstance(cost, dict):
        raise PromotionComparisonError(f"model version {version} is missing cost metadata")
    try:
        fn_cost = float(cost["false_negative_cost"])
        fp_cost = float(cost["false_positive_cost"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PromotionComparisonError(
            f"model version {version} has invalid cost metadata"
        ) from exc
    if not all(math.isfinite(value) and value >= 0 for value in (fn_cost, fp_cost)):
        raise PromotionComparisonError(f"model version {version} has invalid cost values")
    return fn_cost, fp_cost


def _score_model(
    wrapper: Any,
    metadata: dict[str, Any],
    rows: pd.DataFrame,
    version: str,
) -> dict[str, Any]:
    try:
        threshold = float(metadata["threshold"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PromotionComparisonError(f"model version {version} has no valid threshold") from exc
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise PromotionComparisonError(f"model version {version} threshold is outside [0, 1]")

    y_true = rows[TARGET_COL].astype(int).to_numpy()
    if set(np.unique(y_true)) != {0, 1}:
        raise PromotionComparisonError("promotion evaluation requires both target classes")
    model_input = rows.drop(columns=[TARGET_COL])
    try:
        output = wrapper.predict(None, model_input)
    except Exception as exc:
        raise PromotionComparisonError(
            f"prediction failed for model version {version}: {exc}"
        ) from exc
    if not isinstance(output, pd.DataFrame) or len(output) != len(rows):
        raise PromotionComparisonError(
            f"model version {version} returned an invalid prediction table"
        )
    required = {"fraud_probability", "fraud_prediction", "threshold"}
    if not required.issubset(output.columns):
        missing = sorted(required - set(output.columns))
        raise PromotionComparisonError(f"model version {version} output missing columns: {missing}")

    probabilities = pd.to_numeric(output["fraud_probability"], errors="coerce").to_numpy()
    predictions = pd.to_numeric(output["fraud_prediction"], errors="coerce").to_numpy()
    output_thresholds = pd.to_numeric(output["threshold"], errors="coerce").to_numpy()
    if (
        not np.isfinite(probabilities).all()
        or not ((probabilities >= 0) & (probabilities <= 1)).all()
    ):
        raise PromotionComparisonError(
            f"model version {version} returned non-finite or out-of-range probabilities"
        )
    expected_predictions = (probabilities >= threshold).astype(int)
    if not np.array_equal(predictions, expected_predictions):
        raise PromotionComparisonError(
            f"model version {version} predictions do not use its stored threshold"
        )
    if not np.isfinite(output_thresholds).all() or not np.allclose(output_thresholds, threshold):
        raise PromotionComparisonError(
            f"model version {version} output does not report its stored threshold"
        )

    metrics = compute_metrics(y_true, probabilities, threshold)
    fn_cost, fp_cost = _cost_settings(metadata, version)
    metrics["expected_cost"] = expected_cost(y_true, expected_predictions, fn_cost, fp_cost)
    metrics["expected_cost_per_sample"] = metrics["expected_cost"] / len(rows)
    for key in COMPARED_METRICS:
        if key not in metrics or not math.isfinite(float(metrics[key])):
            raise PromotionComparisonError(
                f"model version {version} produced missing or non-finite metric {key}"
            )
    return metrics


def _provenance(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "git": metadata.get("git"),
        "data_fingerprint": metadata.get("data_fingerprint"),
        "dataset": metadata.get("dataset"),
        "threshold_strategy": metadata.get("threshold_strategy"),
        "cost": metadata.get("cost"),
        "random_seed": metadata.get("random_seed"),
    }


def compare_model_versions(
    client: Any,
    model_name: str,
    candidate_mv: Any,
    champion_mv: Any | None,
) -> dict[str, Any]:
    """Build a trace report from immutable model-version references."""
    candidate = _version_ref(candidate_mv, settings.candidate_alias)
    champion = (
        _version_ref(champion_mv, settings.champion_alias) if champion_mv is not None else None
    )
    report: dict[str, Any] = {
        "model_name": model_name,
        "compared_at": datetime.now(UTC).isoformat(),
        "candidate": candidate,
        "champion": champion,
        "evaluation": None,
        "diff": {},
        "gates": {},
        "metric_semantics": {
            "threshold_independent": ["roc_auc", "pr_auc", "brier_score", "log_loss"],
            "threshold_dependent": [
                "precision",
                "recall",
                "f1",
                "confusion_matrix",
                "expected_cost",
                "expected_cost_per_sample",
            ],
            "lower_is_better": [
                "brier_score",
                "log_loss",
                "expected_cost",
                "expected_cost_per_sample",
            ],
        },
        "decision": "blocked",
        "reason": "comparison did not complete",
    }
    try:
        _, candidate_wrapper, candidate_metadata = _load_version(model_name, candidate["version"])
        candidate_manifest = _manifest(candidate_metadata, candidate["version"])
        champion_wrapper = None
        champion_metadata = None
        champion_manifest = None
        if champion is not None:
            _, champion_wrapper, champion_metadata = _load_version(model_name, champion["version"])
            champion_manifest = _manifest(champion_metadata, champion["version"])
            if semantic_evidence_identity(candidate_manifest) != semantic_evidence_identity(
                champion_manifest
            ):
                raise PromotionComparisonError(
                    "candidate and champion promotion evaluation evidence does not match"
                )
            if _cost_settings(candidate_metadata, candidate["version"]) != _cost_settings(
                champion_metadata, champion["version"]
            ):
                raise PromotionComparisonError(
                    "candidate and champion use different expected-cost assumptions"
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = _download_and_verify(
                client, candidate["run_id"], candidate_manifest, root / "candidate"
            )
            if champion is not None and champion_manifest is not None:
                champion_rows = _download_and_verify(
                    client, champion["run_id"], champion_manifest, root / "champion"
                )
                if not rows.equals(champion_rows):
                    raise PromotionComparisonError(
                        "candidate and champion artifacts do not contain identical rows"
                    )

        candidate["provenance"] = _provenance(candidate_metadata)
        candidate["threshold"] = float(candidate_metadata["threshold"])
        candidate["metrics"] = _score_model(
            candidate_wrapper, candidate_metadata, rows, candidate["version"]
        )
        if champion is not None and champion_wrapper is not None and champion_metadata is not None:
            champion["provenance"] = _provenance(champion_metadata)
            champion["threshold"] = float(champion_metadata["threshold"])
            champion["metrics"] = _score_model(
                champion_wrapper, champion_metadata, rows, champion["version"]
            )

        report["evaluation"] = semantic_evidence_identity(candidate_manifest)
        absolute_pr = candidate["metrics"]["pr_auc"] >= settings.min_pr_auc
        absolute_recall = candidate["metrics"]["recall"] >= settings.min_recall
        report["gates"]["min_pr_auc"] = {
            "passed": absolute_pr,
            "actual": candidate["metrics"]["pr_auc"],
            "required": settings.min_pr_auc,
        }
        report["gates"]["min_recall"] = {
            "passed": absolute_recall,
            "actual": candidate["metrics"]["recall"],
            "required": settings.min_recall,
        }
        if champion is None:
            regression_passed = True
            report["gates"]["max_champion_regression"] = {
                "passed": True,
                "not_applicable": True,
                "reason": "no existing champion",
            }
        else:
            for metric in COMPARED_METRICS:
                report["diff"][metric] = candidate["metrics"][metric] - champion["metrics"][metric]
            report["diff"]["confusion_matrix"] = {
                key: candidate["metrics"]["confusion_matrix"][key]
                - champion["metrics"]["confusion_matrix"][key]
                for key in ("tn", "fp", "fn", "tp")
            }
            report["diff"]["threshold"] = candidate["threshold"] - champion["threshold"]
            pr_delta = report["diff"]["pr_auc"]
            regression_passed = pr_delta >= -settings.max_champion_regression
            report["gates"]["max_champion_regression"] = {
                "passed": regression_passed,
                "metric": "pr_auc",
                "delta": pr_delta,
                "minimum_delta": -settings.max_champion_regression,
            }

        if absolute_pr and absolute_recall and regression_passed:
            report["decision"] = (
                "promote_candidate_no_champion"
                if champion is None
                else "candidate_at_least_as_good"
            )
            report["reason"] = "all comparable promotion-evaluation gates passed"
        else:
            report["decision"] = "candidate_worse"
            report["reason"] = "one or more promotion-evaluation gates failed"
    except PromotionComparisonError as exc:
        report["failure"] = {"type": type(exc).__name__, "detail": str(exc)}
        report["reason"] = str(exc)
    except Exception as exc:  # fail closed on unanticipated scorer/artifact failures
        detail = f"unexpected promotion comparison failure: {exc}"
        report["failure"] = {"type": type(exc).__name__, "detail": detail}
        report["reason"] = detail
    return report


def compare_candidate_vs_champion(
    model_name: str | None = None, tracking_uri: str | None = None
) -> dict[str, Any]:
    """Resolve aliases once, then compare immutable versions without mutation."""
    model_name = model_name or settings.model_name
    mlflow.set_tracking_uri(tracking_uri or settings.mlflow_tracking_uri)
    client = mlflow.MlflowClient()
    candidate_mv = get_alias_model_version(client, model_name, settings.candidate_alias)
    if candidate_mv is None:
        return {
            "model_name": model_name,
            "compared_at": datetime.now(UTC).isoformat(),
            "candidate": None,
            "champion": None,
            "evaluation": None,
            "diff": {},
            "gates": {},
            "decision": "no_candidate",
            "reason": "No candidate model version found.",
        }
    champion_mv = get_alias_model_version(client, model_name, settings.champion_alias)
    return compare_model_versions(client, model_name, candidate_mv, champion_mv)


def render_markdown(report: dict[str, Any]) -> str:
    candidate = report.get("candidate") or {}
    champion = report.get("champion") or {}
    evaluation = report.get("evaluation") or {}
    lines = [
        f"# Model comparison report: {report['model_name']}",
        "",
        f"Compared at: {report['compared_at']}",
        "",
        f"**Decision:** `{report['decision']}` — {report['reason']}",
        "",
        f"- evaluation dataset: `{evaluation.get('dataset_id')}`",
        f"- evaluation fingerprint: `{evaluation.get('fingerprint')}`",
        f"- candidate: version {candidate.get('version')} (run `{candidate.get('run_id')}`)",
        f"- champion: version {champion.get('version')} (run `{champion.get('run_id')}`)",
        "",
        "| metric | candidate | champion | candidate - champion |",
        "|---|---:|---:|---:|",
    ]
    for metric in COMPARED_METRICS:
        candidate_value = (candidate.get("metrics") or {}).get(metric)
        champion_value = (champion.get("metrics") or {}).get(metric)
        diff_value = report.get("diff", {}).get(metric)
        lines.append(f"| {metric} | {candidate_value} | {champion_value} | {diff_value} |")
    return "\n".join(lines) + "\n"


def save_reports(report: dict[str, Any]) -> tuple[Path, Path]:
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = settings.reports_dir / "model_comparison.json"
    md_path = settings.reports_dir / "model_comparison.md"
    json_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    md_path.write_text(render_markdown(report))
    return json_path, md_path


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    report = compare_candidate_vs_champion()
    json_path, md_path = save_reports(report)
    logger.info("Wrote %s and %s", json_path, md_path)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
