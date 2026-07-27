"""Compare the `candidate` model version against the current `champion`.

Uses the metrics already recorded on each version's MLflow training run
(rather than re-scoring on raw data) so this can run anywhere with registry
access, independent of the training dataset being present.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import mlflow

from src.config import settings

logger = logging.getLogger(__name__)

COMPARED_METRICS = ["roc_auc", "pr_auc", "recall", "precision", "f1", "log_loss", "brier_score"]


def _model_version_info(client: "mlflow.MlflowClient", model_name: str, alias: str) -> dict | None:
    try:
        mv = client.get_model_version_by_alias(model_name, alias)
    except Exception:
        return None
    run = client.get_run(mv.run_id)
    metrics = dict(run.data.metrics)
    threshold = run.data.params.get("threshold")
    return {
        "alias": alias,
        "version": mv.version,
        "run_id": mv.run_id,
        "metrics": {f"val_{m}": metrics.get(f"val_{m}") for m in COMPARED_METRICS},
        "threshold": float(threshold) if threshold is not None else None,
    }


def compare_candidate_vs_champion(model_name: str | None = None, tracking_uri: str | None = None) -> dict:
    """Build a full comparison report; does not mutate the registry."""
    model_name = model_name or settings.model_name
    mlflow.set_tracking_uri(tracking_uri or settings.mlflow_tracking_uri)
    client = mlflow.MlflowClient()

    candidate = _model_version_info(client, model_name, settings.candidate_alias)
    champion = _model_version_info(client, model_name, settings.champion_alias)

    diff: dict[str, float | None] = {}
    if candidate is None:
        decision, reason = "no_candidate", "No candidate model version found."
    elif champion is None:
        decision = "promote_candidate_no_champion"
        reason = "No existing champion to compare against; candidate would be the first model."
    else:
        for m in COMPARED_METRICS:
            key = f"val_{m}"
            c_val, ch_val = candidate["metrics"].get(key), champion["metrics"].get(key)
            diff[key] = (c_val - ch_val) if (c_val is not None and ch_val is not None) else None
        threshold_diff = None
        if candidate["threshold"] is not None and champion["threshold"] is not None:
            threshold_diff = float(candidate["threshold"]) - float(champion["threshold"])
        diff["threshold"] = threshold_diff

        pr_auc_diff = diff.get("val_pr_auc")
        if pr_auc_diff is not None and pr_auc_diff >= -settings.max_champion_regression:
            decision = "candidate_at_least_as_good"
            reason = (
                f"val_pr_auc diff={pr_auc_diff:.4f} is within the allowed regression "
                f"tolerance of -{settings.max_champion_regression}"
            )
        else:
            decision = "candidate_worse"
            reason = (
                f"val_pr_auc diff={pr_auc_diff} exceeds the allowed regression "
                f"tolerance of -{settings.max_champion_regression}"
                if pr_auc_diff is not None
                else "val_pr_auc missing on one of the versions; cannot confirm candidate is not worse"
            )

    return {
        "model_name": model_name,
        "compared_at": datetime.now(timezone.utc).isoformat(),
        "candidate": candidate,
        "champion": champion,
        "diff": diff,
        "decision": decision,
        "reason": reason,
    }


def render_markdown(report: dict) -> str:
    cand = report["candidate"] or {}
    champ = report["champion"] or {}
    diff = report["diff"] or {}
    lines = [
        f"# Model comparison report: {report['model_name']}",
        "",
        f"Compared at: {report['compared_at']}",
        "",
        f"**Decision:** `{report['decision']}` — {report['reason']}",
        "",
        f"- candidate: version {cand.get('version')} (run `{cand.get('run_id')}`)"
        if cand
        else "- candidate: none",
        f"- champion: version {champ.get('version')} (run `{champ.get('run_id')}`)"
        if champ
        else "- champion: none",
        "",
        "| metric | candidate | champion | diff |",
        "|---|---|---|---|",
    ]
    for m in COMPARED_METRICS:
        key = f"val_{m}"
        c = (cand.get("metrics") or {}).get(key)
        ch = (champ.get("metrics") or {}).get(key)
        lines.append(f"| {m} | {c} | {ch} | {diff.get(key)} |")
    lines.append(f"| threshold | {cand.get('threshold')} | {champ.get('threshold')} | {diff.get('threshold')} |")
    return "\n".join(lines) + "\n"


def save_reports(report: dict) -> tuple[Path, Path]:
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = settings.reports_dir / "model_comparison.json"
    md_path = settings.reports_dir / "model_comparison.md"
    json_path.write_text(json.dumps(report, indent=2, default=str))
    md_path.write_text(render_markdown(report))
    return json_path, md_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    report = compare_candidate_vs_champion()
    json_path, md_path = save_reports(report)
    logger.info("Wrote %s and %s", json_path, md_path)

    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.experiment_name)
    with mlflow.start_run(run_name="model-comparison"):
        mlflow.log_dict(report, "model_comparison.json")
        mlflow.log_artifact(str(md_path))

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
