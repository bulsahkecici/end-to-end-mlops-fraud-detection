"""Fail-closed candidate-to-champion promotion on frozen evaluation rows.

Training assigns only ``candidate``.  This command freezes registry versions,
rescoring candidate and champion on identical promotion-only data before it
may move ``champion``.  Deployment remains a separate operation.
"""

from __future__ import annotations

import json
import logging
import math
import sys
from datetime import UTC, datetime
from typing import Any

import mlflow
import pandas as pd

from src.config import settings
from src.registry.compare import (
    RegistryLookupError,
    compare_model_versions,
    get_alias_model_version,
)

logger = logging.getLogger(__name__)


def _record(checks: dict[str, dict[str, Any]], name: str, passed: bool, detail: str = "") -> None:
    checks[name] = {"passed": bool(passed), "detail": detail}


def _result(
    *,
    promoted: bool,
    model_name: str,
    candidate_version: str | None,
    checks: dict[str, dict[str, Any]],
    comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "promoted": promoted,
        "model_name": model_name,
        "candidate_version": candidate_version,
        "champion_alias": settings.champion_alias,
        "checked_at": datetime.now(UTC).isoformat(),
        "checks": checks,
        "comparison_decision": comparison.get("decision") if comparison else None,
        "comparison": comparison,
        "deployment_changed": False,
    }


def run_promotion_checks(
    model_name: str | None = None, tracking_uri: str | None = None
) -> dict[str, Any]:
    model_name = model_name or settings.model_name
    mlflow.set_tracking_uri(tracking_uri or settings.mlflow_tracking_uri)
    client = mlflow.MlflowClient()
    checks: dict[str, dict[str, Any]] = {}

    try:
        candidate_mv = get_alias_model_version(client, model_name, settings.candidate_alias)
    except RegistryLookupError as exc:
        _record(checks, "candidate_registry_lookup", False, str(exc))
        return _result(
            promoted=False,
            model_name=model_name,
            candidate_version=None,
            checks=checks,
        )
    if candidate_mv is None:
        _record(checks, "candidate_exists", False, "candidate alias does not exist")
        return _result(
            promoted=False,
            model_name=model_name,
            candidate_version=None,
            checks=checks,
        )
    candidate_version = str(candidate_mv.version)
    _record(checks, "candidate_exists", True, f"version={candidate_version}")

    try:
        champion_mv = get_alias_model_version(client, model_name, settings.champion_alias)
        _record(
            checks,
            "champion_registry_lookup",
            True,
            "no existing champion" if champion_mv is None else f"version={champion_mv.version}",
        )
    except RegistryLookupError as exc:
        _record(checks, "champion_registry_lookup", False, str(exc))
        return _result(
            promoted=False,
            model_name=model_name,
            candidate_version=candidate_version,
            checks=checks,
        )

    try:
        pyfunc_model = mlflow.pyfunc.load_model(f"models:/{model_name}/{candidate_version}")
        wrapper = pyfunc_model.unwrap_python_model()
        _record(checks, "model_loadable", True, f"immutable version={candidate_version}")
    except Exception as exc:
        _record(checks, "model_loadable", False, str(exc))
        return _result(
            promoted=False,
            model_name=model_name,
            candidate_version=candidate_version,
            checks=checks,
        )

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
        valid_probability = math.isfinite(proba) and 0.0 <= proba <= 1.0
        _record(checks, "smoke_predict", True, f"fraud_probability={proba}")
        _record(
            checks,
            "predictions_in_valid_range",
            valid_probability,
            f"fraud_probability={proba}",
        )
    except Exception as exc:
        _record(checks, "smoke_predict", False, str(exc))
        _record(checks, "predictions_in_valid_range", False, "smoke predict failed")

    comparison = compare_model_versions(client, model_name, candidate_mv, champion_mv)
    evidence_ok = comparison["decision"] != "blocked" and comparison.get("evaluation") is not None
    _record(checks, "canonical_evaluation_evidence", evidence_ok, comparison["reason"])
    comparison_gates = comparison.get("gates", {})
    for gate_name in ("min_pr_auc", "min_recall", "max_champion_regression"):
        gate = comparison_gates.get(gate_name)
        _record(
            checks,
            gate_name,
            bool(gate and gate.get("passed")),
            json.dumps(gate, sort_keys=True) if gate else "gate could not be evaluated",
        )

    eligible = all(check["passed"] for check in checks.values())
    if eligible:
        try:
            current_candidate = get_alias_model_version(
                client, model_name, settings.candidate_alias
            )
            stable = (
                current_candidate is not None
                and str(current_candidate.version) == candidate_version
                and str(current_candidate.run_id) == str(candidate_mv.run_id)
            )
            detail = (
                f"candidate remained at version={candidate_version}"
                if stable
                else "candidate alias moved or disappeared during evaluation"
            )
            _record(checks, "candidate_alias_stable", stable, detail)
        except RegistryLookupError as exc:
            _record(checks, "candidate_alias_stable", False, str(exc))
    else:
        _record(checks, "candidate_alias_stable", False, "not checked because earlier gates failed")

    eligible = all(check["passed"] for check in checks.values())
    trace = _result(
        promoted=eligible,
        model_name=model_name,
        candidate_version=candidate_version,
        checks=checks,
        comparison=comparison,
    )
    try:
        client.log_dict(str(candidate_mv.run_id), trace, "promotion/promotion_decision.json")
        _record(checks, "trace_persisted", True, "promotion/promotion_decision.json")
    except Exception as exc:
        _record(checks, "trace_persisted", False, str(exc))
        eligible = False

    if eligible:
        try:
            client.set_registered_model_alias(
                model_name, settings.champion_alias, candidate_version
            )
            logger.info(
                "Promoted %s version %s to alias '%s'",
                model_name,
                candidate_version,
                settings.champion_alias,
            )
        except Exception as exc:
            _record(checks, "champion_alias_updated", False, str(exc))
            eligible = False
    if not eligible:
        failed = [name for name, check in checks.items() if not check["passed"]]
        logger.warning(
            "Promotion blocked for %s version %s; failed checks: %s",
            model_name,
            candidate_version,
            failed,
        )

    final_result = _result(
        promoted=eligible,
        model_name=model_name,
        candidate_version=candidate_version,
        checks=checks,
        comparison=comparison,
    )
    if trace != final_result:
        try:
            client.log_dict(
                str(candidate_mv.run_id), final_result, "promotion/promotion_decision.json"
            )
        except Exception:
            logger.exception("Could not update final promotion trace")
    return final_result


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    result = run_promotion_checks()
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result["promoted"] else 1)


if __name__ == "__main__":
    main()
