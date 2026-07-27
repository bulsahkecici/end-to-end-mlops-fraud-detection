"""End-to-end training entrypoint.

Pipeline: ingest -> validate -> temporal split -> fit preprocessing+model
(train split only) -> evaluate + select threshold (validation split) ->
final evaluation (test split) -> log a single MLflow pyfunc artifact ->
register a new model version under the ``candidate`` alias.

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
from src.data.validation import validate_raw_transactions
from src.features.pipeline import ColumnAligner, build_preprocessor, infer_schema
from src.modeling.evaluate import compute_metrics
from src.modeling.mlflow_wrapper import FraudModelWrapper
from src.modeling.threshold import select_threshold
from src.modeling.validation import split_data
from src.utils.repro import fingerprint_file, get_env_info, get_git_info, set_global_seed

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

    train_df, val_df, test_df, split_summary = split_data(
        df,
        strategy=split_strategy,
        train_ratio=settings.train_ratio,
        val_ratio=settings.val_ratio,
        test_ratio=settings.test_ratio,
        seed=seed,
    )
    for name, summary in split_summary.items():
        logger.info("split=%s summary=%s", name, summary)

    y_train = train_df[TARGET_COL].astype(int)
    y_val = val_df[TARGET_COL].astype(int)
    y_test = test_df[TARGET_COL].astype(int)
    X_train_raw = train_df.drop(columns=[TARGET_COL])
    X_val_raw = val_df.drop(columns=[TARGET_COL])
    X_test_raw = test_df.drop(columns=[TARGET_COL])

    # Schema + all fitting happens on the TRAIN split only -> no leakage.
    schema = infer_schema(X_train_raw)
    aligner = ColumnAligner(
        numeric_cols=schema.numeric_cols, categorical_cols=schema.categorical_cols
    ).fit(X_train_raw)
    X_train_aligned = aligner.transform(X_train_raw)
    X_val_aligned = aligner.transform(X_val_raw)

    preprocessor = build_preprocessor(schema)
    X_train_t = preprocessor.fit_transform(X_train_aligned)
    X_val_t = preprocessor.transform(X_val_aligned)

    params = default_lgbm_params(seed)
    if lgbm_overrides:
        params.update(lgbm_overrides)
    classifier = LGBMClassifier(**params)
    classifier.fit(
        X_train_t,
        y_train,
        eval_set=[(X_val_t, y_val)],
        eval_metric="auc",
        callbacks=[lgb.early_stopping(stopping_rounds=20, verbose=False), lgb.log_evaluation(0)],
    )

    # Assemble the final pipeline from already-fitted steps: Pipeline.predict
    # simply chains transform/predict in order, it does not require having
    # been fit via Pipeline.fit() itself.
    pipeline = Pipeline(
        steps=[("aligner", aligner), ("preprocessor", preprocessor), ("classifier", classifier)]
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

    val_metrics = compute_metrics(y_val, val_proba, threshold)
    test_proba = pipeline.predict_proba(X_test_raw)[:, 1]
    test_metrics = compute_metrics(y_test, test_proba, threshold)

    metadata = {
        "model_name": settings.model_name,
        "feature_schema": {
            "numeric_cols": schema.numeric_cols,
            "categorical_cols": schema.categorical_cols,
            "dropped_cols": schema.dropped_cols,
        },
        "threshold": threshold,
        "threshold_strategy": threshold_strategy,
        "dataset": {
            "data_source": data_source,
            "split_strategy": split_strategy,
            "sampling_strategy": sampling_strategy,
            "sample_rows": sample_rows,
            "splits": split_summary,
        },
        "cost": {
            "false_negative_cost": settings.false_negative_cost,
            "false_positive_cost": settings.false_positive_cost,
        },
        "git": get_git_info(settings.project_root),
        "env": get_env_info(),
        "data_fingerprint": (
            {f.name: fingerprint_file(f) for f in source_files}
            if source_files
            else {"source": "synthetic", "n_rows": str(n_synthetic)}
        ),
        "created_at": datetime.now(UTC).isoformat(),
        "random_seed": seed,
    }

    mlflow.set_tracking_uri(tracking_uri or settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.experiment_name)

    with mlflow.start_run(run_name=f"ieee-lgbm-{data_source}") as run:
        mlflow.log_params(
            {
                **{f"lgbm_{k}": v for k, v in params.items()},
                "data_source": data_source,
                "sampling_strategy": sampling_strategy,
                "sample_rows": str(sample_rows) if sample_rows is not None else "all",
                "split_strategy": split_strategy,
                "seed": seed,
                "threshold_strategy": threshold_strategy,
                "threshold": threshold,
                "false_negative_cost": settings.false_negative_cost,
                "false_positive_cost": settings.false_positive_cost,
                "n_train": len(train_df),
                "n_val": len(val_df),
                "n_test": len(test_df),
            }
        )
        for k, v in val_metrics.items():
            if k != "confusion_matrix":
                mlflow.log_metric(f"val_{k}", v)
        for k, v in test_metrics.items():
            if k != "confusion_matrix":
                mlflow.log_metric(f"test_{k}", v)
        mlflow.log_dict(metadata, "metadata.json")
        mlflow.log_dict(split_summary, "split_summary.json")
        mlflow.log_dict(val_metrics, "val_metrics.json")
        mlflow.log_dict(test_metrics, "test_metrics.json")

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

        with tempfile.TemporaryDirectory() as tmp:
            pipeline_path = Path(tmp) / "pipeline.joblib"
            joblib.dump(pipeline, pipeline_path)
            metadata_path = Path(tmp) / "metadata.json"
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
            "test_metrics": test_metrics,
            "feature_schema": metadata["feature_schema"],
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
            }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the IEEE-CIS fraud detection model.")
    parser.add_argument("--data-source", choices=["ieee", "synthetic"], default="ieee")
    parser.add_argument("--sample-rows", type=int, default=None)
    parser.add_argument("--n-synthetic", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=None)
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
        register=not args.no_register,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
