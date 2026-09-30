"""End-to-end training entrypoint.

Pipeline: ingest -> validate -> temporal split -> separate selection-validation
from promotion evaluation -> fit preprocessing+model (train split only) ->
optional calibration and threshold selection (selection-validation only) ->
log a single MLflow pyfunc artifact plus frozen promotion rows -> register a
new model version under the ``candidate`` alias. Final-test reporting is a
separate explicit operation in ``src.modeling.final_test``.

The new version is deliberately NOT promoted to ``champion``/production
here — see ``src/registry/promote.py`` for the gated promotion step.

Usage:
    python -m src.modeling.train --data-source synthetic   # no real data needed
    python -m src.modeling.train --data-source ieee --sample-rows 50000
"""

from __future__ import annotations

import argparse
import json
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import joblib
import lightgbm as lgb
import mlflow
import mlflow.pyfunc
import pandas as pd
from lightgbm import LGBMClassifier
from mlflow.models import ModelSignature
from mlflow.types import ColSpec, DataType, Schema, TensorSpec
from sklearn.pipeline import Pipeline

from src.config import settings
from src.data.ingest import load_train, make_synthetic_transactions
from src.data.validation import summarize_split, validate_raw_transactions
from src.features.pipeline import (
    BASELINE_CATEGORICAL_STRATEGY,
    ColumnAligner,
    build_preprocessor,
    infer_schema,
)
from src.modeling.calibration import (
    fit_probability_calibrator,
    split_calibration_and_selection,
)
from src.modeling.evaluate import compute_metrics
from src.modeling.experiment import build_experiment_record
from src.modeling.mlflow_wrapper import FraudModelWrapper
from src.modeling.promotion_evaluation import (
    ARTIFACT_DIR,
    split_selection_and_promotion,
    write_evaluation_artifact,
)
from src.modeling.threshold import select_threshold
from src.modeling.validation import split_data
from src.utils.repro import fingerprint_file_contents, get_env_info, get_git_info, set_global_seed

logger = logging.getLogger(__name__)

TARGET_COL = "isFraud"


def default_lgbm_params(seed: int) -> dict:
    return {
        "objective": "binary",
        "n_estimators": 500,
        "num_leaves": 31,
        "learning_rate": 0.05,
        "colsample_bytree": 0.9,
        "subsample": 0.8,
        "subsample_freq": 5,
        "random_state": seed,
        "n_jobs": -1,
        "verbosity": -1,
    }


def run_training(
    data_source: str = "ieee",
    sample_rows: int | None = None,
    n_synthetic: int = 4000,
    seed: int | None = None,
    split_strategy: str | None = None,
    sampling_strategy: str | None = None,
    threshold_strategy: str | None = None,
    tracking_uri: str | None = None,
    register: bool = True,
    lgbm_overrides: dict | None = None,
    categorical_strategy: str = BASELINE_CATEGORICAL_STRATEGY,
    calibration_strategy: str = "none",
    experiment_variant_id: str | None = None,
    cat_nunique_max: int = 200,
    debug_return: bool = False,
) -> dict:
    """Run the full training pipeline once and return a result summary dict."""
    seed = seed if seed is not None else settings.random_seed
    set_global_seed(seed)
    split_strategy = split_strategy or settings.split_strategy
    sampling_strategy = sampling_strategy or settings.sampling_strategy
    threshold_strategy = threshold_strategy or settings.threshold_strategy
    sample_rows = sample_rows if sample_rows is not None else settings.sample_rows

    source_files: list[Path] = []
    if data_source == "synthetic":
        df = make_synthetic_transactions(n=n_synthetic, seed=seed)
    elif data_source == "ieee":
        df = load_train(sample_rows=sample_rows, sampling_strategy=sampling_strategy, seed=seed)
        source_files = [
            settings.ieee_data_dir / "train_transaction.csv",
            settings.ieee_data_dir / "train_identity.csv",
        ]
    else:
        raise ValueError(f"Unknown data_source={data_source!r}, expected 'ieee' or 'synthetic'")

    validate_raw_transactions(df, require_target=True)

    train_df, validation_pool, test_df, outer_split_summary = split_data(
        df,
        strategy=split_strategy,
        train_ratio=settings.train_ratio,
        val_ratio=settings.val_ratio,
        test_ratio=settings.test_ratio,
        seed=seed,
        summarize_final_test=False,
    )
    selection_pool, promotion_df = split_selection_and_promotion(
        validation_pool, strategy=split_strategy, seed=seed
    )
    calibration_df, val_df = split_calibration_and_selection(
        selection_pool, strategy=split_strategy, seed=seed
    )
    split_summary = {
        "train": outer_split_summary["train"],
        "calibration_fit": summarize_split(calibration_df, "calibration_fit"),
        "selection_validation": summarize_split(val_df, "selection_validation"),
        "validation": summarize_split(val_df, "validation"),
        "promotion_evaluation": summarize_split(promotion_df, "promotion_evaluation"),
        "test": outer_split_summary["test"],
    }
    for name, summary in split_summary.items():
        logger.info("split=%s summary=%s", name, summary)

    y_train = train_df[TARGET_COL].astype(int)
    y_calibration = calibration_df[TARGET_COL].astype(int)
    y_val = val_df[TARGET_COL].astype(int)
    X_train_raw = train_df.drop(columns=[TARGET_COL])
    X_calibration_raw = calibration_df.drop(columns=[TARGET_COL])
    X_val_raw = val_df.drop(columns=[TARGET_COL])

    # Schema + all fitting happens on the TRAIN split only -> no leakage.
    schema = infer_schema(
        X_train_raw,
        cat_nunique_max=cat_nunique_max,
        categorical_strategy=categorical_strategy,
    )
    aligner = ColumnAligner(
        numeric_cols=schema.numeric_cols, categorical_cols=schema.categorical_cols
    ).fit(X_train_raw)
    X_train_aligned = aligner.transform(X_train_raw)
    X_calibration_aligned = aligner.transform(X_calibration_raw)
    X_val_aligned = aligner.transform(X_val_raw)

    preprocessor = build_preprocessor(schema)
    X_train_t = preprocessor.fit_transform(X_train_aligned)
    X_calibration_t = preprocessor.transform(X_calibration_aligned)
    X_val_t = preprocessor.transform(X_val_aligned)

    params = default_lgbm_params(seed)
    if lgbm_overrides:
        params.update(lgbm_overrides)
    classifier = LGBMClassifier(**params)
    classifier.fit(
        X_train_t,
        y_train,
        eval_set=[(X_calibration_t, y_calibration)],
        eval_metric="auc",
        callbacks=[lgb.early_stopping(stopping_rounds=20, verbose=False), lgb.log_evaluation(0)],
    )

    uncalibrated_val_proba = classifier.predict_proba(X_val_t)[:, 1]
    uncalibrated_threshold_info = select_threshold(
        y_val,
        uncalibrated_val_proba,
        strategy=threshold_strategy,
        fixed_threshold=settings.fixed_threshold,
        target_recall=settings.target_recall,
        fn_cost=settings.false_negative_cost,
        fp_cost=settings.false_positive_cost,
    )
    uncalibrated_val_metrics = compute_metrics(
        y_val,
        uncalibrated_val_proba,
        uncalibrated_threshold_info["threshold"],
        fn_cost=settings.false_negative_cost,
        fp_cost=settings.false_positive_cost,
    )
    fitted_classifier = fit_probability_calibrator(
        classifier,
        X_calibration_t,
        y_calibration,
        strategy=calibration_strategy,
        seed=seed,
    )

    # Assemble the final pipeline from already-fitted steps: Pipeline.predict
    # simply chains transform/predict in order, it does not require having
    # been fit via Pipeline.fit() itself.
    pipeline = Pipeline(
        steps=[
            ("aligner", aligner),
            ("preprocessor", preprocessor),
            ("classifier", fitted_classifier),
        ]
    )

    val_proba = pipeline.predict_proba(X_val_raw)[:, 1]
    threshold_info = select_threshold(
        y_val,
        val_proba,
        strategy=threshold_strategy,
        fixed_threshold=settings.fixed_threshold,
        target_recall=settings.target_recall,
        fn_cost=settings.false_negative_cost,
        fp_cost=settings.false_positive_cost,
    )
    threshold = threshold_info["threshold"]

    val_metrics = compute_metrics(
        y_val,
        val_proba,
        threshold,
        fn_cost=settings.false_negative_cost,
        fp_cost=settings.false_positive_cost,
    )

    source_data_fingerprint: dict[str, object] = (
        {f.name: fingerprint_file_contents(f) for f in source_files}
        if source_files
        else {
            "source": "synthetic",
            "generator": "make_synthetic_transactions:v1",
            "n_rows": n_synthetic,
            "seed": seed,
        }
    )
    variant_id = experiment_variant_id or (f"lgbm-{categorical_strategy}-{calibration_strategy}")
    experiment = build_experiment_record(
        variant_id=variant_id,
        categorical_strategy=categorical_strategy,
        calibration_strategy=calibration_strategy,
        model_params=params,
        random_seed=seed,
        split_strategy=split_strategy,
        calibration_fit_rows=calibration_df,
        selection_rows=val_df,
        source_data_fingerprint=source_data_fingerprint,
    )

    mlflow.set_tracking_uri(tracking_uri or settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.experiment_name)

    with (
        tempfile.TemporaryDirectory() as tmp,
        mlflow.start_run(run_name=f"ieee-lgbm-{data_source}") as run,
    ):
        temporary_dir = Path(tmp)
        evaluation_dir = temporary_dir / ARTIFACT_DIR
        promotion_manifest = write_evaluation_artifact(
            promotion_df,
            evaluation_dir,
            data_source=data_source,
            split_strategy=split_strategy,
            source_data_fingerprint=source_data_fingerprint,
            random_seed=seed,
        )
        metadata = {
            "model_name": settings.model_name,
            "feature_schema": {
                "numeric_cols": schema.numeric_cols,
                "categorical_cols": schema.categorical_cols,
                "high_cardinality_cols": schema.high_cardinality_cols,
                "dropped_cols": schema.dropped_cols,
            },
            "threshold": threshold,
            "threshold_strategy": threshold_strategy,
            "dataset": {
                "data_source": data_source,
                "split_strategy": split_strategy,
                "sampling_strategy": sampling_strategy,
                "sample_rows": sample_rows,
                "split_ratios": {
                    "train": settings.train_ratio,
                    "validation": settings.val_ratio,
                    "test": settings.test_ratio,
                },
                "splits": split_summary,
            },
            "experiment": experiment,
            "promotion_evaluation": promotion_manifest,
            "cost": {
                "false_negative_cost": settings.false_negative_cost,
                "false_positive_cost": settings.false_positive_cost,
            },
            "git": get_git_info(settings.project_root),
            "env": get_env_info(),
            "data_fingerprint": source_data_fingerprint,
            "created_at": datetime.now(UTC).isoformat(),
            "random_seed": seed,
        }
        mlflow.log_params(
            {
                **{f"lgbm_{k}": v for k, v in params.items()},
                "data_source": data_source,
                "sampling_strategy": sampling_strategy,
                "sample_rows": str(sample_rows) if sample_rows is not None else "all",
                "split_strategy": split_strategy,
                "seed": seed,
                "experiment_variant_id": variant_id,
                "feature_strategy": experiment["feature_strategy"],
                "categorical_strategy": categorical_strategy,
                "calibration_strategy": calibration_strategy,
                "selection_evaluation_fingerprint": experiment["selection_evaluation"][
                    "fingerprint"
                ],
                "threshold_strategy": threshold_strategy,
                "threshold": threshold,
                "false_negative_cost": settings.false_negative_cost,
                "false_positive_cost": settings.false_positive_cost,
                "n_train": len(train_df),
                "n_calibration_fit": len(calibration_df),
                "n_selection_validation": len(val_df),
                "n_val": len(val_df),
                "n_promotion_evaluation": len(promotion_df),
                "n_test": len(test_df),
            }
        )
        for k, v in val_metrics.items():
            if k != "confusion_matrix":
                mlflow.log_metric(f"val_{k}", v)
        for k, v in uncalibrated_val_metrics.items():
            if k != "confusion_matrix":
                mlflow.log_metric(f"val_uncalibrated_{k}", v)
        mlflow.log_dict(metadata, "metadata.json")
        mlflow.log_dict(split_summary, "split_summary.json")
        mlflow.log_dict(val_metrics, "val_metrics.json")
        mlflow.log_dict(uncalibrated_val_metrics, "val_uncalibrated_metrics.json")
        mlflow.log_dict(experiment, "experiment.json")
        mlflow.log_artifacts(str(evaluation_dir), artifact_path=ARTIFACT_DIR)

        input_example = X_train_raw[schema.use_cols].head(5).reset_index(drop=True)
        # Match the declared `double` dtype in input_schema below exactly —
        # newer mlflow versions validate input_example against the logged
        # signature at log-model time, and int64 columns (e.g. TransactionDT,
        # which has no missing values in this sample) would otherwise fail
        # that check even though the pipeline itself handles either dtype.
        for col in schema.numeric_cols:
            input_example[col] = input_example[col].astype("float64")
        example_proba = pipeline.predict_proba(input_example)[:, 1]
        example_output = pd.DataFrame(
            {
                "fraud_probability": example_proba,
                "fraud_prediction": (example_proba >= threshold).astype(int),
                "threshold": threshold,
            }
        )
        # Every input column is declared optional: the ColumnAligner step
        # already handles missing/extra/reordered columns at inference time,
        # and MLflow's default (all-required) schema enforcement would
        # otherwise reject exactly the "missing column" requests we designed
        # the pipeline to tolerate, before our code ever runs.
        input_cols: list[ColSpec | TensorSpec] = []
        input_cols += [ColSpec(DataType.double, c, required=False) for c in schema.numeric_cols]
        input_cols += [ColSpec(DataType.string, c, required=False) for c in schema.categorical_cols]
        input_schema = Schema(input_cols)
        output_schema = mlflow.models.infer_signature(input_example, example_output).outputs
        signature = ModelSignature(inputs=input_schema, outputs=output_schema)

        pipeline_path = temporary_dir / "pipeline.joblib"
        joblib.dump(pipeline, pipeline_path)
        metadata_path = temporary_dir / "metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2, default=str))

        mlflow.pyfunc.log_model(
            artifact_path="model",
            python_model=FraudModelWrapper(),
            artifacts={"pipeline": str(pipeline_path), "metadata": str(metadata_path)},
            code_paths=[str(settings.project_root / "src")],
            signature=signature,
            input_example=input_example,
            registered_model_name=settings.model_name if register else None,
        )

        version = None
        if register:
            client = mlflow.MlflowClient()
            versions = client.search_model_versions(f"run_id='{run.info.run_id}'")
            version = str(versions[0].version) if versions else None
            if version:
                client.set_registered_model_alias(
                    settings.model_name, settings.candidate_alias, version
                )
                logger.info(
                    "Registered %s version %s with alias '%s'",
                    settings.model_name,
                    version,
                    settings.candidate_alias,
                )

        result = {
            "run_id": run.info.run_id,
            "model_name": settings.model_name,
            "model_version": version,
            "threshold": threshold,
            "threshold_strategy": threshold_strategy,
            "val_metrics": val_metrics,
            "uncalibrated_val_metrics": uncalibrated_val_metrics,
            "feature_schema": metadata["feature_schema"],
            "experiment": experiment,
            "promotion_evaluation": promotion_manifest,
        }
        if debug_return:
            # Test-only escape hatch: exposes the in-memory fitted pipeline
            # and validation split so tests can assert train/serve parity
            # without re-deriving them from scratch.
            result["_debug"] = {
                "pipeline": pipeline,
                "X_val_raw": X_val_raw,
                "y_val": y_val,
                "val_proba": val_proba,
                "promotion_rows": promotion_df,
            }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the IEEE-CIS fraud detection model.")
    parser.add_argument("--data-source", choices=["ieee", "synthetic"], default="ieee")
    parser.add_argument("--sample-rows", type=int, default=None)
    parser.add_argument("--n-synthetic", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--categorical-strategy",
        choices=["ordinal_drop_high_cardinality", "frequency_high_cardinality"],
        default=BASELINE_CATEGORICAL_STRATEGY,
    )
    parser.add_argument(
        "--calibration-strategy", choices=["none", "sigmoid", "isotonic"], default="none"
    )
    parser.add_argument("--experiment-variant-id", default=None)
    parser.add_argument("--no-register", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    result = run_training(
        data_source=args.data_source,
        sample_rows=args.sample_rows,
        n_synthetic=args.n_synthetic,
        seed=args.seed,
        categorical_strategy=args.categorical_strategy,
        calibration_strategy=args.calibration_strategy,
        experiment_variant_id=args.experiment_variant_id,
        register=not args.no_register,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
