from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.monitoring.drift import (
    BREACH,
    NOT_EVALUATED,
    PASS,
    WARN,
    MonitoringContract,
    MonitoringContractError,
    MonitoringWindow,
    apply_deployment_state,
    compute_drift_report,
    dataframe_fingerprint,
    main,
    render_markdown,
    report_exit_code,
    save_reports,
)

REFERENCE_WINDOW = MonitoringWindow.from_values(
    "2026-01-01T00:00:00+00:00", "2026-01-31T23:59:59+00:00", "reference"
)
CURRENT_WINDOW = MonitoringWindow.from_values(
    "2026-02-01T00:00:00+00:00", "2026-02-28T23:59:59+00:00", "current"
)


def _contract(*, predictions: bool = False, top_k: int = 3) -> MonitoringContract:
    monitoring: dict[str, object] = {
        "numeric_bin_count": 4,
        "categorical_top_k": top_k,
    }
    if predictions:
        monitoring.update(
            {
                "probability_column": "fraud_probability",
                "decision_column": "fraud_prediction",
            }
        )
    return MonitoringContract.from_dict(
        {
            "contract_schema_version": "1.0",
            "model": {
                "model_name": "ieee-fraud-model",
                "model_version": "7",
                "run_id": "run-immutable-7",
                "deployment_id": "deployment-7",
                "threshold": 0.42,
                "threshold_source": "model_metadata",
            },
            "feature_schema": {
                "numeric_cols": ["amount", "constant", "all_null"],
                "categorical_cols": ["product"],
            },
            "monitoring": monitoring,
        }
    )


def _frames(*, predictions: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    reference = pd.DataFrame(
        {
            "amount": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
            "constant": [5.0] * 6,
            "all_null": [np.nan] * 6,
            "product": ["W", "W", "C", "C", "R", None],
        }
    )
    current = reference.copy()
    if predictions:
        reference["fraud_probability"] = [0.05, 0.15, 0.25, 0.55, 0.75, 0.95]
        current["fraud_probability"] = [0.10, 0.20, 0.30, 0.60, 0.80, 0.90]
        reference["fraud_prediction"] = [0, 0, 0, 1, 1, 1]
        current["fraud_prediction"] = [0, 0, 0, 1, 1, 1]
    return reference, current


def _report(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    *,
    contract: MonitoringContract | None = None,
    generated_at: str = "2026-03-01T00:00:00+00:00",
) -> dict:
    return compute_drift_report(
        reference,
        current,
        contract=contract or _contract(),
        reference_source="warehouse.reference.v1",
        current_source="warehouse.current.v1",
        reference_window=REFERENCE_WINDOW,
        current_window=CURRENT_WINDOW,
        generated_at=generated_at,
    )


def _feature(report: dict, name: str) -> dict:
    return next(item for item in report["semantic"]["features"] if item["feature"] == name)


def _check(report: dict, check_id: str) -> dict:
    return next(item for item in report["semantic"]["checks"] if item["check_id"] == check_id)


def _transaction_artifacts(directory: Path) -> list[Path]:
    return [
        path
        for path in directory.iterdir()
        if path.name.startswith(".") and path.suffix in {".tmp", ".bak"}
    ]


def _contract_payload(*, predictions: bool = False) -> dict:
    return _contract(predictions=predictions).as_dict()


def _write_cli_inputs(
    tmp_path: Path,
    *,
    current: pd.DataFrame | None = None,
    predictions: bool = False,
) -> list[str]:
    reference, default_current = _frames(predictions=predictions)
    current = default_current if current is None else current
    reference_path = tmp_path / "reference.csv"
    current_path = tmp_path / "current.csv"
    contract_path = tmp_path / "contract.json"
    reference.to_csv(reference_path, index=False)
    current.to_csv(current_path, index=False)
    contract_path.write_text(json.dumps(_contract_payload(predictions=predictions)))
    return [
        "--reference",
        str(reference_path),
        "--current",
        str(current_path),
        "--contract",
        str(contract_path),
        "--reference-source",
        "warehouse.reference.v1",
        "--current-source",
        "warehouse.current.v1",
        "--reference-window-start",
        REFERENCE_WINDOW.start,
        "--reference-window-end",
        REFERENCE_WINDOW.end,
        "--current-window-start",
        CURRENT_WINDOW.start,
        "--current-window-end",
        CURRENT_WINDOW.end,
        "--output-json",
        str(tmp_path / "report.json"),
        "--output-markdown",
        str(tmp_path / "report.md"),
    ]


def test_semantic_report_is_deterministic_and_timestamp_is_separate():
    reference, current = _frames(predictions=True)
    first = _report(reference, current, contract=_contract(predictions=True), generated_at="a")
    second = _report(reference, current, contract=_contract(predictions=True), generated_at="b")

    assert first["generated_at"] != second["generated_at"]
    assert first["semantic"] == second["semantic"]
    assert first["semantic_identity_sha256"] == second["semantic_identity_sha256"]


def test_row_and_column_order_do_not_change_semantics_or_fingerprints():
    reference, current = _frames(predictions=True)
    shuffled_reference = reference.sample(frac=1, random_state=9).loc[
        :, reversed(reference.columns)
    ]
    shuffled_current = current.sample(frac=1, random_state=3).loc[:, reversed(current.columns)]

    original = _report(reference, current, contract=_contract(predictions=True))
    shuffled = _report(
        shuffled_reference,
        shuffled_current,
        contract=_contract(predictions=True),
    )

    assert dataframe_fingerprint(reference) == dataframe_fingerprint(shuffled_reference)
    assert dataframe_fingerprint(current) == dataframe_fingerprint(shuffled_current)
    assert original["semantic"] == shuffled["semantic"]


def test_numeric_bins_are_reference_derived_and_detect_distribution_shift():
    reference, current = _frames()
    current["amount"] = [1000.0, 1100.0, 1200.0, 1300.0, 1400.0, 1500.0]
    report = _report(reference, current)
    amount = _feature(report, "amount")

    assert amount["reference_bins"]["strategy"] == "reference_quantiles"
    assert max(amount["reference_bins"]["edges"]) <= reference["amount"].max()
    assert amount["distribution"]["total_variation_distance"] > 0.2
    assert _check(report, "feature.amount.distribution_shift")["status"] == BREACH


def test_constant_and_all_null_references_are_safe_and_explicit():
    reference, current = _frames()
    current["constant"] = [6.0] * len(current)
    current["all_null"] = [1.0] * len(current)
    report = _report(reference, current)

    constant = _feature(report, "constant")
    all_null = _feature(report, "all_null")
    assert constant["reference_bins"]["strategy"] == "constant_reference"
    assert constant["standardized_mean_shift"] is None
    assert _check(report, "feature.constant.standardized_mean_shift")["status"] == NOT_EVALUATED
    assert all_null["reference_bins"]["strategy"] == "unavailable_all_nonfinite"
    assert _check(report, "feature.all_null.distribution_shift")["status"] == NOT_EVALUATED
    assert _check(report, "feature.all_null.missing_rate_shift")["status"] == BREACH


def test_missing_current_feature_breaches_and_extra_column_is_bounded_warning():
    reference, current = _frames()
    current = current.drop(columns=["amount"])
    current["unexpected"] = "value"
    report = _report(reference, current)

    assert _check(report, "feature.amount.column_presence")["status"] == BREACH
    assert _check(report, "schema.unexpected_current_columns")["status"] == WARN
    extras = report["semantic"]["schema_observations"]["unexpected_current_columns"]
    assert extras["count"] == 1
    assert extras["reported_count"] == 1
    assert "unexpected" not in json.dumps(extras)


def test_numeric_nan_inf_and_bad_coercion_never_emit_nonstandard_json():
    reference, current = _frames()
    current["amount"] = [10.0, np.nan, np.inf, -np.inf, "bad", "50"]
    report = _report(reference, current)

    assert _feature(report, "amount")["current"]["invalid_rate"] > 0
    serialized = json.dumps(report, allow_nan=False)
    assert "NaN" not in serialized
    assert "Infinity" not in serialized


def test_all_current_null_numeric_column_is_not_a_healthy_pass():
    reference, current = _frames()
    current["amount"] = np.nan
    report = _report(reference, current)

    assert _check(report, "feature.amount.missing_rate_shift")["status"] == BREACH
    assert _check(report, "feature.amount.distribution_shift")["status"] == BREACH
    assert report_exit_code(report) == 1


def test_reference_missing_model_feature_fails_closed():
    reference, current = _frames()
    with pytest.raises(MonitoringContractError, match="reference is missing model features"):
        _report(reference.drop(columns=["amount"]), current)


def test_categorical_buckets_are_bounded_and_do_not_expose_raw_values():
    reference, current = _frames()
    reference["product"] = [f"sensitive-category-{index}" for index in range(len(reference))]
    current["product"] = ["sensitive-category-0", "never-seen", None, "x", "y", "z"]
    report = _report(reference, current, contract=_contract(top_k=2))
    product = _feature(report, "product")
    buckets = product["buckets"]["current"]

    assert len(buckets) == 5  # top-K + OTHER + MISSING + UNKNOWN
    assert [item["bucket"] for item in buckets][-3:] == ["OTHER", "MISSING", "UNKNOWN"]
    assert next(item for item in buckets if item["bucket"] == "UNKNOWN")["share"] > 0
    payload = json.dumps(product)
    assert "sensitive-category" not in payload
    assert "never-seen" not in payload


def test_categorical_bucket_order_is_deterministic_for_ties_and_missingness():
    reference, current = _frames()
    reference["product"] = ["b", "a", "c", "b", "a", None]
    first = _feature(_report(reference, current), "product")["buckets"]
    second = _feature(_report(reference.iloc[::-1], current.iloc[::-1]), "product")["buckets"]
    assert first == second


def test_all_null_categorical_reference_routes_current_values_to_unknown():
    reference, current = _frames()
    reference["product"] = [None] * len(reference)
    current["product"] = ["new"] * len(current)
    report = _report(reference, current)
    product = _feature(report, "product")

    assert product["reference_unique_categories"] == 0
    assert [item["bucket"] for item in product["buckets"]["reference"]] == [
        "OTHER",
        "MISSING",
        "UNKNOWN",
    ]
    assert _check(report, "feature.product.unknown_rate")["status"] == BREACH


def test_provenance_schema_and_threshold_are_explicit_and_fingerprinted():
    reference, current = _frames()
    report = _report(reference, current)
    provenance = report["semantic"]["provenance"]
    monitoring = report["semantic"]["monitoring_contract"]

    assert provenance["model_name"] == "ieee-fraud-model"
    assert provenance["model_version"] == "7"
    assert provenance["run_id"] == "run-immutable-7"
    assert provenance["deployment_id"] == "deployment-7"
    assert provenance["threshold"] == 0.42
    assert provenance["threshold_source"] == "model_metadata"
    assert len(monitoring["schema_fingerprint_sha256"]) == 64
    assert len(monitoring["config_fingerprint_sha256"]) == 64


def test_missing_required_model_provenance_is_rejected():
    payload = _contract_payload()
    del payload["model"]["run_id"]
    with pytest.raises(MonitoringContractError, match="missing required fields: run_id"):
        MonitoringContract.from_dict(payload)


def test_validated_deployment_state_supplies_deployment_id_and_rejects_mismatch(tmp_path):
    payload = _contract_payload()
    payload["model"]["deployment_id"] = None
    contract = MonitoringContract.from_dict(payload)
    state = {
        "schema_version": 1,
        "deployment_id": "deployment-from-state",
        "action": "deploy",
        "model_name": "ieee-fraud-model",
        "model_version": "7",
        "run_id": "run-immutable-7",
        "source_alias": "champion",
        "deployed_at": "2026-02-28T00:00:00+00:00",
        "previous_deployment_id": None,
        "previous_model_version": None,
        "rollback_of_deployment_id": None,
        "git_commit": None,
        "git_branch": None,
        "git_dirty": None,
    }
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(state))

    updated = apply_deployment_state(contract, path)
    assert updated.provenance.deployment_id == "deployment-from-state"

    state["run_id"] = "different-run"
    path.write_text(json.dumps(state))
    with pytest.raises(MonitoringContractError, match="identity does not match"):
        apply_deployment_state(contract, path)


def test_prediction_probability_and_decision_distribution_are_separate():
    reference, current = _frames(predictions=True)
    report = _report(reference, current, contract=_contract(predictions=True))
    prediction = report["semantic"]["prediction_distribution"]

    assert prediction["status"] == "EVALUATED"
    assert prediction["probability"]["reference_mean_probability"] == pytest.approx(0.45)
    assert prediction["decisions"]["reference_positive_decision_rate"] == 0.5
    assert prediction["threshold"] == {
        "value": 0.42,
        "source": "model_metadata",
        "status": "AVAILABLE",
    }


def test_all_missing_reference_probability_fails_closed():
    reference, current = _frames(predictions=True)
    reference["fraud_probability"] = np.nan

    with pytest.raises(MonitoringContractError, match="no valid values"):
        _report(reference, current, contract=_contract(predictions=True))


def test_valid_reference_and_all_missing_current_probability_breaches_availability():
    reference, current = _frames(predictions=True)
    current["fraud_probability"] = np.nan
    report = _report(reference, current, contract=_contract(predictions=True))
    probability = report["semantic"]["prediction_distribution"]["probability"]

    assert probability["status"] == NOT_EVALUATED
    assert probability["reason"] == "current_has_no_valid_probabilities"
    assert _check(report, "prediction.probability.valid_data_availability")["status"] == BREACH
    assert _check(report, "prediction.probability.distribution_shift")["status"] == NOT_EVALUATED
    assert _check(report, "prediction.probability.mean_shift")["status"] == NOT_EVALUATED
    assert report["semantic"]["summary"]["overall_status"] == BREACH
    assert report_exit_code(report) == 1


def test_all_missing_reference_decision_fails_closed():
    reference, current = _frames(predictions=True)
    reference["fraud_prediction"] = np.nan

    with pytest.raises(MonitoringContractError, match="no valid values"):
        _report(reference, current, contract=_contract(predictions=True))


def test_valid_reference_and_all_missing_current_decision_breaches_availability():
    reference, current = _frames(predictions=True)
    current["fraud_prediction"] = np.nan
    report = _report(reference, current, contract=_contract(predictions=True))
    decisions = report["semantic"]["prediction_distribution"]["decisions"]

    assert decisions["status"] == NOT_EVALUATED
    assert decisions["reason"] == "current_has_no_valid_decisions"
    assert _check(report, "prediction.decision.valid_data_availability")["status"] == BREACH
    assert _check(report, "prediction.decision.positive_rate_shift")["status"] == NOT_EVALUATED
    assert report["semantic"]["summary"]["overall_status"] == BREACH
    assert report_exit_code(report) == 1


def test_invalid_probability_and_decision_values_are_explicit_breaches():
    reference, current = _frames(predictions=True)
    current["fraud_probability"] = [-0.1, 1.1, 0.2, 0.3, 0.4, 0.5]
    current["fraud_prediction"] = [-1, 2, 0, 1, 0, 1]
    report = _report(reference, current, contract=_contract(predictions=True))

    assert _check(report, "prediction.probability.invalid_rate_increase")["status"] == BREACH
    assert _check(report, "prediction.decision.invalid_rate_increase")["status"] == BREACH
    assert report_exit_code(report) == 1


def test_prediction_unavailable_is_explicit_and_label_prevalence_is_not_used():
    reference, current = _frames()
    reference["isFraud"] = [0, 0, 0, 0, 0, 1]
    current["isFraud"] = [1, 1, 1, 1, 1, 1]
    report = _report(reference, current)
    prediction = report["semantic"]["prediction_distribution"]

    assert prediction["status"] == NOT_EVALUATED
    assert prediction["reason"] == "prediction_columns_not_configured"
    assert "fraud_rate" not in json.dumps(prediction).lower()
    assert _check(report, "prediction.availability")["status"] == NOT_EVALUATED


def test_check_records_have_fixed_fields_vocab_and_exit_semantics():
    reference, current = _frames()
    passing = _report(reference, current)
    current["amount"] = current["amount"] * 100
    breach = _report(reference, current)

    assert report_exit_code(passing) == 0
    assert report_exit_code(breach) == 1
    for check in breach["semantic"]["checks"]:
        assert set(check) == {
            "check_id",
            "feature",
            "metric",
            "observed",
            "threshold",
            "status",
            "severity",
        }
        assert check["status"] in {PASS, WARN, BREACH, NOT_EVALUATED}


def test_cli_missing_required_arguments_exits_nonzero():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_cli_malformed_input_returns_contract_error(tmp_path, capsys):
    args = _write_cli_inputs(tmp_path)
    (tmp_path / "current.csv").write_text("")

    assert main(args) == 2
    assert "drift monitoring failed" in capsys.readouterr().err


def test_cli_missing_input_file_returns_exit_two(tmp_path, capsys):
    args = _write_cli_inputs(tmp_path)
    args[args.index("--current") + 1] = str(tmp_path / "missing.csv")

    assert main(args) == 2
    assert "drift monitoring failed" in capsys.readouterr().err


def test_cli_malformed_contract_returns_exit_two(tmp_path, capsys):
    args = _write_cli_inputs(tmp_path)
    contract_path = Path(args[args.index("--contract") + 1])
    contract_path.write_text("{")

    assert main(args) == 2
    assert "drift monitoring failed" in capsys.readouterr().err


def test_cli_output_write_failure_returns_exit_two(tmp_path, capsys):
    args = _write_cli_inputs(tmp_path)
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("file")
    args[args.index("--output-json") + 1] = str(blocked_parent / "report.json")

    assert main(args) == 2
    assert "drift monitoring failed" in capsys.readouterr().err


def test_cli_pass_warn_and_breach_exit_codes(tmp_path):
    pass_dir = tmp_path / "pass"
    pass_dir.mkdir()
    assert main(_write_cli_inputs(pass_dir)) == 0

    warn_dir = tmp_path / "warn"
    warn_dir.mkdir()
    _, warning_current = _frames()
    warning_current["extra"] = 1
    assert main(_write_cli_inputs(warn_dir, current=warning_current)) == 0
    warning_report = json.loads((warn_dir / "report.json").read_text())
    assert warning_report["semantic"]["summary"]["overall_status"] == WARN

    breach_dir = tmp_path / "breach"
    breach_dir.mkdir()
    _, breached_current = _frames()
    breached_current = breached_current.drop(columns=["amount"])
    assert main(_write_cli_inputs(breach_dir, current=breached_current)) == 1


def test_explicit_outputs_do_not_overwrite_without_opt_in(tmp_path, capsys):
    args = _write_cli_inputs(tmp_path)
    assert main(args) == 0
    original = (tmp_path / "report.json").read_text()

    assert main(args) == 2
    assert (tmp_path / "report.json").read_text() == original
    assert "refusing to overwrite" in capsys.readouterr().err


def test_save_reports_and_markdown_use_explicit_paths(tmp_path):
    reference, current = _frames(predictions=True)
    report = _report(reference, current, contract=_contract(predictions=True))
    json_path = tmp_path / "versioned-report-001.json"
    markdown_path = tmp_path / "versioned-report-001.md"

    save_reports(report, json_path, markdown_path)
    parsed = json.loads(json_path.read_text(), parse_constant=lambda value: pytest.fail(value))
    markdown = markdown_path.read_text()

    assert parsed["semantic_identity_sha256"] == report["semantic_identity_sha256"]
    assert "# Drift report" in markdown
    assert "## Prediction distribution" in markdown
    assert "Overall status" in markdown
    provenance = report["semantic"]["provenance"]
    summary = report["semantic"]["summary"]
    prediction_status = report["semantic"]["prediction_distribution"]["status"]
    assert (
        f"Model: `{provenance['model_name']}` version `{provenance['model_version']}`" in markdown
    )
    assert f"Run ID: `{provenance['run_id']}`" in markdown
    assert f"Deployment ID: `{provenance['deployment_id']}`" in markdown
    assert f"**Overall status:** {summary['overall_status']}" in markdown
    assert f"Status: {prediction_status}" in markdown
    assert render_markdown(report) == markdown
    assert _transaction_artifacts(tmp_path) == []


def test_save_reports_rejects_same_path_and_existing_unrelated_file(tmp_path):
    reference, current = _frames()
    report = _report(reference, current)
    output = tmp_path / "existing.json"
    output.write_text("unrelated")

    with pytest.raises(MonitoringContractError, match="refusing to overwrite"):
        save_reports(report, output)
    assert output.read_text() == "unrelated"
    with pytest.raises(MonitoringContractError, match="paths must differ"):
        save_reports(report, tmp_path / "same", tmp_path / "same")


def test_second_output_failure_removes_newly_published_first_output(tmp_path, monkeypatch):
    reference, current = _frames()
    report = _report(reference, current)
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    real_link = os.link

    def fail_markdown_link(source, destination):
        if Path(destination) == markdown_path:
            raise OSError("simulated second-output publication failure")
        real_link(source, destination)

    monkeypatch.setattr("src.monitoring.drift.os.link", fail_markdown_link)
    with pytest.raises(OSError, match="second-output"):
        save_reports(report, json_path, markdown_path)

    assert not json_path.exists()
    assert not markdown_path.exists()
    assert _transaction_artifacts(tmp_path) == []


def test_second_output_failure_restores_prior_outputs_on_overwrite(tmp_path, monkeypatch):
    reference, current = _frames()
    report = _report(reference, current)
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    json_path.write_text("prior-json")
    markdown_path.write_text("prior-markdown")
    real_replace = os.replace

    def fail_markdown_install(source, destination):
        if Path(destination) == markdown_path and Path(source).suffix == ".tmp":
            raise OSError("simulated second-output publication failure")
        real_replace(source, destination)

    monkeypatch.setattr("src.monitoring.drift.os.replace", fail_markdown_install)
    with pytest.raises(OSError, match="second-output"):
        save_reports(report, json_path, markdown_path, overwrite=True)

    assert json_path.read_text() == "prior-json"
    assert markdown_path.read_text() == "prior-markdown"
    assert _transaction_artifacts(tmp_path) == []


def test_overwrite_disabled_pair_fails_before_any_publication(tmp_path):
    reference, current = _frames()
    report = _report(reference, current)
    json_path = tmp_path / "new-report.json"
    markdown_path = tmp_path / "existing-report.md"
    markdown_path.write_text("prior-markdown")

    with pytest.raises(MonitoringContractError, match="refusing to overwrite"):
        save_reports(report, json_path, markdown_path)

    assert not json_path.exists()
    assert markdown_path.read_text() == "prior-markdown"
    assert _transaction_artifacts(tmp_path) == []


def test_explicit_overwrite_success_replaces_both_outputs_atomically(tmp_path):
    reference, current = _frames()
    report = _report(reference, current)
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    json_path.write_text("prior-json")
    markdown_path.write_text("prior-markdown")

    save_reports(report, json_path, markdown_path, overwrite=True)

    assert (
        json.loads(json_path.read_text())["semantic_identity_sha256"]
        == report["semantic_identity_sha256"]
    )
    assert markdown_path.read_text() == render_markdown(report)
    assert _transaction_artifacts(tmp_path) == []


def test_single_output_overwrite_failure_restores_prior_output(tmp_path, monkeypatch):
    reference, current = _frames()
    report = _report(reference, current)
    json_path = tmp_path / "report.json"
    json_path.write_text("prior-json")
    real_replace = os.replace

    def fail_json_install(source, destination):
        if Path(destination) == json_path and Path(source).suffix == ".tmp":
            raise OSError("simulated single-output publication failure")
        real_replace(source, destination)

    monkeypatch.setattr("src.monitoring.drift.os.replace", fail_json_install)
    with pytest.raises(OSError, match="single-output"):
        save_reports(report, json_path, overwrite=True)

    assert json_path.read_text() == "prior-json"
    assert _transaction_artifacts(tmp_path) == []


def test_unsupported_values_fail_closed_without_hash_seed_dependent_fingerprints():
    repository = Path(__file__).resolve().parents[2]
    supported_script = (
        "import pandas as pd; "
        "from src.monitoring.drift import dataframe_fingerprint; "
        "print(dataframe_fingerprint(pd.DataFrame({'a':[1, 2.5, None],"
        "'b':['x', 'y', 'z']})))"
    )
    unsupported_script = (
        "import pandas as pd; "
        "from src.monitoring.drift import MonitoringContractError, dataframe_fingerprint; "
        "\ntry:\n dataframe_fingerprint(pd.DataFrame({'a':[{'alpha', 'beta'}]}))"
        "\nexcept MonitoringContractError as exc:\n print(type(exc).__name__, str(exc))"
    )

    def run(script: str, seed: str) -> str:
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        return subprocess.check_output(
            [sys.executable, "-c", script], cwd=repository, env=environment, text=True
        ).strip()

    assert run(supported_script, "1") == run(supported_script, "2")
    first_error = run(unsupported_script, "1")
    second_error = run(unsupported_script, "2")
    assert first_error == second_error
    assert first_error == (
        "MonitoringContractError unsupported input value type for deterministic "
        "canonicalization: set"
    )
