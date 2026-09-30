"""Explicit final-test release reporting, separate from routine training."""

from __future__ import annotations

import argparse
import json
from typing import Any

import mlflow
import mlflow.pyfunc

from src.config import settings
from src.data.ingest import load_train, make_synthetic_transactions
from src.data.validation import validate_raw_transactions
from src.modeling.evaluate import compute_metrics
from src.modeling.experiment import FINGERPRINT_ALGORITHM, fingerprint_rows
from src.modeling.validation import split_data
from src.utils.repro import fingerprint_file_contents

TARGET_COL = "isFraud"


def _load_source(metadata: dict[str, Any]):
    dataset = metadata["dataset"]
    data_source = dataset["data_source"]
    seed = int(metadata["random_seed"])
    if data_source == "synthetic":
        n_rows = int(metadata["data_fingerprint"]["n_rows"])
        rows = make_synthetic_transactions(n=n_rows, seed=seed)
        observed_fingerprint: dict[str, Any] = {
            "source": "synthetic",
            "generator": "make_synthetic_transactions:v1",
            "n_rows": n_rows,
            "seed": seed,
        }
    elif data_source == "ieee":
        rows = load_train(
            sample_rows=dataset["sample_rows"],
            sampling_strategy=dataset["sampling_strategy"],
            seed=seed,
        )
        source_files = [
            settings.ieee_data_dir / "train_transaction.csv",
            settings.ieee_data_dir / "train_identity.csv",
        ]
        observed_fingerprint = {path.name: fingerprint_file_contents(path) for path in source_files}
    else:
        raise ValueError(f"Unsupported model data_source={data_source!r}")

    if observed_fingerprint != metadata["data_fingerprint"]:
        raise ValueError("Current source data fingerprint does not match the trained model")
    return rows


def evaluate_final_test(model_uri: str, tracking_uri: str | None = None) -> dict[str, Any]:
    """Score one frozen model on its reserved final split exactly once by request."""
    mlflow.set_tracking_uri(tracking_uri or settings.mlflow_tracking_uri)
    loaded = mlflow.pyfunc.load_model(model_uri)
    wrapper = loaded.unwrap_python_model()
    metadata = wrapper.metadata

    rows = _load_source(metadata)
    validate_raw_transactions(rows, require_target=True)
    dataset = metadata["dataset"]
    ratios = dataset["split_ratios"]
    _, _, final_test, summaries = split_data(
        rows,
        strategy=dataset["split_strategy"],
        train_ratio=float(ratios["train"]),
        val_ratio=float(ratios["validation"]),
        test_ratio=float(ratios["test"]),
        seed=int(metadata["random_seed"]),
        summarize_final_test=True,
    )
    X_test = final_test.drop(columns=[TARGET_COL])
    y_test = final_test[TARGET_COL].astype(int)
    predictions = wrapper.predict(None, X_test)
    threshold = float(metadata["threshold"])
    metrics = compute_metrics(
        y_test,
        predictions["fraud_probability"].to_numpy(),
        threshold,
        fn_cost=float(metadata["cost"]["false_negative_cost"]),
        fp_cost=float(metadata["cost"]["false_positive_cost"]),
    )
    fingerprint = fingerprint_rows(final_test.reset_index(drop=True))
    synthetic = dataset["data_source"] == "synthetic"
    return {
        "purpose": "final_test_release_report",
        "model_uri": model_uri,
        "experiment_variant_id": metadata["experiment"]["variant_id"],
        "evidence_label": ("synthetic_plumbing_only" if synthetic else "real_ieee_cis_final_test"),
        "must_not_be_used_for_model_selection_or_promotion": True,
        "final_test_identity": {
            "dataset_id": f"{dataset['data_source']}:{dataset['split_strategy']}:final-test",
            "dataset_version": fingerprint,
            "fingerprint": fingerprint,
            "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
            "row_count": int(len(final_test)),
            "summary": summaries["test"],
            "source_data_fingerprint": metadata["data_fingerprint"],
        },
        "metrics": metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Explicitly evaluate a model on final test.")
    parser.add_argument("--model-uri", required=True)
    parser.add_argument("--tracking-uri", default=None)
    args = parser.parse_args()
    report = evaluate_final_test(args.model_uri, tracking_uri=args.tracking_uri)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
