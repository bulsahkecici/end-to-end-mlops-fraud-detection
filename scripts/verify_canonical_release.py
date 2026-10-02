"""Fail closed on inconsistent safe canonical release evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.canonical_release import semantic_hash


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
    assert metadata["data_fingerprint"] == dataset["training_source_fingerprint"]
    assert metadata["dataset"]["data_source"] == "ieee"
    assert metadata["dataset"]["sample_rows"] == config["sample_rows"]
    assert metadata["dataset"]["split_strategy"] == config["split_strategy"]
    assert metadata["dataset"]["sampling_strategy"] == config["sampling_strategy"]
    assert metadata["experiment"]["model_params"] == config["model_params"]
    assert metadata["experiment"]["categorical_strategy"] == config["categorical_strategy"]
    assert metadata["experiment"]["calibration_strategy"] == config["calibration_strategy"]
    assert metadata["threshold_strategy"] == config["threshold_strategy"]
    assert metadata["random_seed"] == config["random_seed"]
    assert metadata["git"]["dirty"] is False
    for split_name, experiment_key in (
        ("calibration_fit", "calibration_fit"),
        ("selection_validation", "selection_evaluation"),
    ):
        assert (
            metadata["experiment"][experiment_key]["fingerprint"]
            == splits[split_name]["fingerprint"]
        )
    assert (
        metadata["promotion_evaluation"]["fingerprint"]
        == splits["promotion_evaluation"]["fingerprint"]
    )
    assert promotion["promoted"] is True
    assert promotion["candidate_version"] == version
    assert promotion["deployment_changed"] is False
    assert all(check["passed"] for check in promotion["checks"].values())
    assert deployment["model_version"] == version and deployment["run_id"] == run_id
    assert deployment["model_name"] == config["model_name"]
    assert deployment["action"] == "deploy" and deployment["source_alias"] == "champion"
    if final:
        report = read("final_test_metrics")
        assert report["model_uri"] == uri
        assert report["evidence_label"] == "real_ieee_cis_final_test"
        assert report["must_not_be_used_for_model_selection_or_promotion"] is True
        identity = report["final_test_identity"]
        assert identity["fingerprint"] == splits["final_test"]["fingerprint"]
        assert identity["row_count"] == splits["final_test"]["rows"]
        assert identity["source_data_fingerprint"] == metadata["data_fingerprint"]
        assert report["metrics"]["threshold"] == metadata["threshold"]
        serving = read("serving_manifest")
        assert serving["ready"]["model_source"] == uri
        assert serving["ready"]["model_version"] == version
        assert serving["ready"]["run_id"] == run_id
        assert serving["threshold"] == metadata["threshold"]
        assert all(serving["checks"].values())
    return {"verified": True, "model_uri": uri, "run_id": run_id, "final_report": final}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("releases/ieee-cis-v1"))
    parser.add_argument("--pre-final", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify_release(args.directory, final=not args.pre_final), sort_keys=True))


if __name__ == "__main__":
    main()
