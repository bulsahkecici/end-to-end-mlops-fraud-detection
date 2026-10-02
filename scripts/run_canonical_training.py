"""Replay the preregistered configuration in an operator-selected isolated store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import mlflow

from scripts.canonical_release import semantic_hash, write_json
from scripts.verify_canonical_release import require
from src.config import settings
from src.modeling.train import run_training
from src.utils.repro import fingerprint_file_contents


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    source = Path("releases/ieee-cis-v1")
    config = json.loads((source / "canonical_config.json").read_text())
    freeze = json.loads((source / "preregistration.json").read_text())
    dataset = json.loads((source / "dataset_manifest.json").read_text())
    require(semantic_hash(config) == freeze["config_sha256"], "Configuration changed")
    require(semantic_hash(dataset) == freeze["dataset_sha256"], "Dataset manifest changed")
    for name, entry in dataset["files"].items():
        require(
            fingerprint_file_contents(settings.ieee_data_dir / name) == entry["sha256"],
            f"Source hash mismatch: {name}",
        )
    output = args.output_directory
    require(not (output / "training_manifest.json").exists(), "Refusing to overwrite training")
    require(settings.mlflow_tracking_uri.startswith("sqlite:"), "Use an isolated SQLite store")
    settings.sample_rows = config["sample_rows"]
    settings.random_seed = config["random_seed"]
    settings.split_strategy = config["split_strategy"]
    settings.sampling_strategy = config["sampling_strategy"]
    settings.threshold_strategy = config["threshold_strategy"]
    settings.train_ratio = config["split_ratios"]["train"]
    settings.val_ratio = config["split_ratios"]["development"]
    settings.test_ratio = config["split_ratios"]["final_test"]
    settings.false_negative_cost = config["cost"]["false_negative"]
    settings.false_positive_cost = config["cost"]["false_positive"]
    settings.model_name = config["model_name"]
    settings.experiment_name = config["experiment_name"]
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    client = mlflow.MlflowClient()
    require(
        client.get_experiment_by_name(settings.experiment_name) is None,
        "Canonical replay requires a fresh experiment/store",
    )
    client.create_experiment(
        settings.experiment_name, artifact_location=(output / "mlartifacts").resolve().as_uri()
    )
    result = run_training(
        data_source=config["data_source"],
        seed=config["random_seed"],
        categorical_strategy=config["categorical_strategy"],
        calibration_strategy=config["calibration_strategy"],
        cat_nunique_max=config["cat_nunique_max"],
        lgbm_overrides=config["model_params"],
        experiment_variant_id=config["variant_id"],
    )
    write_json(output / "training_manifest.json", result)
    metadata = json.loads(
        Path(client.download_artifacts(result["run_id"], "metadata.json")).read_text()
    )
    write_json(output / "model_metadata.json", metadata)
    print(json.dumps({"run_id": result["run_id"], "model_version": result["model_version"]}))


if __name__ == "__main__":
    main()
