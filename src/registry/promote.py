"""Gated promotion: assign the `champion` alias to the `candidate` model
version only after it passes a fixed set of checks. A new training run
never becomes `champion` automatically (see src/modeling/train.py, which
only ever assigns `candidate`).

Checks performed:
    1. a candidate model version exists
    2. it is loadable via mlflow.pyfunc
    3. it has a logged input signature
    4. a smoke prediction succeeds and returns a probability in [0, 1]
    5. validation PR-AUC >= MIN_PR_AUC
    6. validation recall >= MIN_RECALL
    7. not a severe regression vs the current champion (via src/registry/compare.py)

Safe to re-run: promoting an already-champion version, or re-running after
a failed attempt, does not corrupt state.

Usage:
    python -m src.registry.promote
"""

from __future__ import annotations

import json
import logging
import sys

import mlflow
import pandas as pd

from src.config import settings
from src.registry.compare import compare_candidate_vs_champion

logger = logging.getLogger(__name__)


def _record(checks: dict, name: str, passed: bool, detail: str = "") -> None:
    checks[name] = {"passed": bool(passed), "detail": detail}


def run_promotion_checks(model_name: str | None = None, tracking_uri: str | None = None) -> dict:
    model_name = model_name or settings.model_name
    mlflow.set_tracking_uri(tracking_uri or settings.mlflow_tracking_uri)
    client = mlflow.MlflowClient()
    checks: dict[str, dict] = {}

    try:
        candidate_mv = client.get_model_version_by_alias(model_name, settings.candidate_alias)
    except Exception as exc:
        _record(checks, "candidate_exists", False, str(exc))
        return {"promoted": False, "candidate_version": None, "checks": checks}
    _record(checks, "candidate_exists", True, f"version={candidate_mv.version}")

    try:
        pyfunc_model = mlflow.pyfunc.load_model(f"models:/{model_name}@{settings.candidate_alias}")
        wrapper = pyfunc_model.unwrap_python_model()
        _record(checks, "model_loadable", True)
    except Exception as exc:
        _record(checks, "model_loadable", False, str(exc))
        return {"promoted": False, "candidate_version": str(candidate_mv.version), "checks": checks}

    signature = pyfunc_model.metadata.signature
    _record(
        checks,
        "signature_present",
        signature is not None,
        "" if signature else "no signature logged",
    )

    try:
        smoke_output = wrapper.predict(None, pd.DataFrame([{}]))
        proba = float(smoke_output["fraud_probability"].iloc[0])
        _record(checks, "smoke_predict", True, f"fraud_probability={proba}")
        _record(checks, "predictions_in_valid_range", 0.0 <= proba <= 1.0, f"got {proba}")
    except Exception as exc:
        _record(checks, "smoke_predict", False, str(exc))
        _record(checks, "predictions_in_valid_range", False, "smoke predict failed")

    run_metrics = dict(client.get_run(candidate_mv.run_id).data.metrics)
    val_pr_auc = run_metrics.get("val_pr_auc")
    val_recall = run_metrics.get("val_recall")
    _record(
        checks,
        "min_pr_auc",
        val_pr_auc is not None and val_pr_auc >= settings.min_pr_auc,
        f"val_pr_auc={val_pr_auc}, required>={settings.min_pr_auc}",
    )
    _record(
        checks,
        "min_recall",
        val_recall is not None and val_recall >= settings.min_recall,
        f"val_recall={val_recall}, required>={settings.min_recall}",
    )

    comparison = compare_candidate_vs_champion(model_name=model_name, tracking_uri=tracking_uri)
    no_regression = comparison["decision"] in (
        "promote_candidate_no_champion",
        "candidate_at_least_as_good",
    )
    _record(checks, "no_severe_champion_regression", no_regression, comparison["reason"])

    all_passed = all(c["passed"] for c in checks.values())
    if all_passed:
        client.set_registered_model_alias(model_name, settings.champion_alias, candidate_mv.version)
        logger.info(
            "Promoted %s version %s to alias '%s'",
            model_name,
            candidate_mv.version,
            settings.champion_alias,
        )
    else:
        failed = [name for name, c in checks.items() if not c["passed"]]
        logger.warning(
            "Promotion blocked for %s version %s; failed checks: %s",
            model_name,
            candidate_mv.version,
            failed,
        )

    return {
        "promoted": all_passed,
        "candidate_version": str(candidate_mv.version),
        "champion_alias": settings.champion_alias,
        "checks": checks,
        "comparison_decision": comparison["decision"],
    }


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    result = run_promotion_checks()
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result["promoted"] else 1)


if __name__ == "__main__":
    main()
