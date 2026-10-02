"""Fail closed on inconsistent safe canonical release evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.canonical_release import semantic_hash


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise ValueError(detail)


def verify_release(directory: Path, *, final: bool = True) -> dict:
    def read(name: str) -> dict:
        return json.loads((directory / f"{name}.json").read_text())

    config, dataset, splits, freeze = (
        read("canonical_config"),
        read("dataset_manifest"),
        read("split_manifest"),
        read("preregistration"),
    )
    for name, value in (("config", config), ("dataset", dataset), ("splits", splits)):
        if semantic_hash(value) != freeze[f"{name}_sha256"]:
            raise ValueError(f"Frozen {name} identity changed")
    training, promotion, deployment = (
        read("training_manifest"),
        read("promotion_manifest"),
        read("deployment_manifest"),
    )
    metadata = read("model_metadata")
    version, run_id = training["model_version"], training["run_id"]
    uri = f"models:/{config['model_name']}/{version}"
    require(
        metadata["data_fingerprint"] == dataset["training_source_fingerprint"],
        'Evidence mismatch: metadata["data_fingerprint"] == dataset["training_source_fing',
    )
    require(
        metadata["dataset"]["data_source"] == "ieee",
        'Evidence mismatch: metadata["dataset"]["data_source"] == "ieee"',
    )
    require(
        metadata["dataset"]["sample_rows"] == config["sample_rows"],
        'Evidence mismatch: metadata["dataset"]["sample_rows"] == config["sample_rows"]',
    )
    require(
        metadata["dataset"]["split_strategy"] == config["split_strategy"],
        'Evidence mismatch: metadata["dataset"]["split_strategy"] == config["split_strate',
    )
    require(
        metadata["dataset"]["sampling_strategy"] == config["sampling_strategy"],
        'Evidence mismatch: metadata["dataset"]["sampling_strategy"] == config["sampling_',
    )
    require(
        metadata["experiment"]["model_params"] == config["model_params"],
        'Evidence mismatch: metadata["experiment"]["model_params"] == config["model_param',
    )
    require(
        metadata["experiment"]["categorical_strategy"] == config["categorical_strategy"],
        'Evidence mismatch: metadata["experiment"]["categorical_strategy"] == config["cat',
    )
    require(
        metadata["experiment"]["calibration_strategy"] == config["calibration_strategy"],
        'Evidence mismatch: metadata["experiment"]["calibration_strategy"] == config["cal',
    )
    require(
        metadata["threshold_strategy"] == config["threshold_strategy"],
        'Evidence mismatch: metadata["threshold_strategy"] == config["threshold_strategy"',
    )
    require(
        metadata["random_seed"] == config["random_seed"],
        'Evidence mismatch: metadata["random_seed"] == config["random_seed"]',
    )
    require(
        metadata["git"]["dirty"] is False, 'Evidence mismatch: metadata["git"]["dirty"] is False'
    )
    for split_name, experiment_key in (
        ("calibration_fit", "calibration_fit"),
        ("selection_validation", "selection_evaluation"),
    ):
        require(
            metadata["experiment"][experiment_key]["fingerprint"]
            == splits[split_name]["fingerprint"],
            'Evidence mismatch: metadata["experiment"][experiment_key]["fingerprint"]        ',
        )
    require(
        metadata["promotion_evaluation"]["fingerprint"]
        == splits["promotion_evaluation"]["fingerprint"],
        'Evidence mismatch: metadata["promotion_evaluation"]["fingerprint"]         == sp',
    )
    require(promotion["promoted"] is True, 'Evidence mismatch: promotion["promoted"] is True')
    require(
        promotion["candidate_version"] == version,
        'Evidence mismatch: promotion["candidate_version"] == version',
    )
    require(
        promotion["deployment_changed"] is False,
        'Evidence mismatch: promotion["deployment_changed"] is False',
    )
    require(
        all(check["passed"] for check in promotion["checks"].values()),
        'Evidence mismatch: all(check["passed"] for check in promotion["checks"].values()',
    )
    require(
        deployment["model_version"] == version and deployment["run_id"] == run_id,
        'Evidence mismatch: deployment["model_version"] == version and deployment["run_id',
    )
    require(
        deployment["model_name"] == config["model_name"],
        'Evidence mismatch: deployment["model_name"] == config["model_name"]',
    )
    require(
        deployment["action"] == "deploy" and deployment["source_alias"] == "champion",
        'Evidence mismatch: deployment["action"] == "deploy" and deployment["source_alias',
    )
    if final:
        report = read("final_test_metrics")
        require(report["model_uri"] == uri, 'Evidence mismatch: report["model_uri"] == uri')
        require(
            report["evidence_label"] == "real_ieee_cis_final_test",
            'Evidence mismatch: report["evidence_label"] == "real_ieee_cis_final_test"',
        )
        require(
            report["must_not_be_used_for_model_selection_or_promotion"] is True,
            'Evidence mismatch: report["must_not_be_used_for_model_selection_or_promotion"] i',
        )
        identity = report["final_test_identity"]
        require(
            identity["fingerprint"] == splits["final_test"]["fingerprint"],
            'Evidence mismatch: identity["fingerprint"] == splits["final_test"]["fingerprint"',
        )
        require(
            identity["row_count"] == splits["final_test"]["rows"],
            'Evidence mismatch: identity["row_count"] == splits["final_test"]["rows"]',
        )
        require(
            identity["source_data_fingerprint"] == metadata["data_fingerprint"],
            'Evidence mismatch: identity["source_data_fingerprint"] == metadata["data_fingerp',
        )
        require(
            report["metrics"]["threshold"] == metadata["threshold"],
            'Evidence mismatch: report["metrics"]["threshold"] == metadata["threshold"]',
        )
        serving = read("serving_manifest")
        require(
            serving["ready"]["model_source"] == f"version:{version}",
            'Evidence mismatch: serving["ready"]["model_source"] == f"version:{version}"',
        )
        require(
            serving["ready"]["model_version"] == version,
            'Evidence mismatch: serving["ready"]["model_version"] == version',
        )
        require(
            serving["ready"]["run_id"] == run_id,
            'Evidence mismatch: serving["ready"]["run_id"] == run_id',
        )
        require(
            serving["threshold"] == metadata["threshold"],
            'Evidence mismatch: serving["threshold"] == metadata["threshold"]',
        )
        require(
            all(serving["checks"].values()), 'Evidence mismatch: all(serving["checks"].values())'
        )
    return {"verified": True, "model_uri": uri, "run_id": run_id, "final_report": final}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("releases/ieee-cis-v1"))
    parser.add_argument("--pre-final", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify_release(args.directory, final=not args.pre_final), sort_keys=True))


if __name__ == "__main__":
    main()
