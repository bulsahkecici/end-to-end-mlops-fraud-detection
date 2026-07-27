# Architecture

## Pipeline overview

```
IEEE-CIS CSVs (or synthetic data)
        │
        ▼
src/data/ingest.py + sampling.py + validation.py
  - deterministic, time-span-preserving sampling (not head-of-file)
  - structural validation (required columns, binary target, no dup ids)
        │
        ▼
src/modeling/validation.py: temporal split (train/val/test)
        │
        ▼
src/features/pipeline.py: infer_schema(X_train)  <- schema decided on TRAIN ONLY
        │
        ▼
ColumnAligner.fit(X_train)          <- records expected columns
preprocessor.fit_transform(X_train) <- imputer/encoder fit on TRAIN ONLY
LGBMClassifier.fit(..., eval_set=[X_val])  <- early stopping on VAL
        │
        ▼
Pipeline(aligner, preprocessor, classifier)  <- one fitted object
        │
        ▼
src/modeling/threshold.py: select_threshold() on VAL predictions
src/modeling/evaluate.py: compute_metrics() on VAL and TEST
        │
        ▼
src/modeling/mlflow_wrapper.py: FraudModelWrapper (pyfunc)
  wraps the fitted Pipeline + threshold + metadata
        │
        ▼
mlflow.pyfunc.log_model(...)  <- ONE artifact, signature + input_example
mlflow registered version, alias = "candidate"
        │
        ▼
src/registry/promote.py  <- gate: loadable, signature present, smoke
                             predict valid, PR-AUC/recall thresholds,
                             not a regression vs "champion"
        │  (all checks pass)
        ▼
alias = "champion"
        │
        ▼
src/api/app.py (FastAPI)
  loads models:/<name>@champion (falls back to legacy "Production" stage)
  at startup; POST /predict calls the SAME fitted Pipeline used at
  training time — no separate preprocessing code path exists.
```

## Why one shared `Pipeline`

The project's original bug was that training encoded categorical features
as native pandas `category` dtype (consumed by LightGBM directly) while the
serving code re-implemented a hand-written category→integer mapping. The
two were never guaranteed to agree.

The fix is structural, not a patch: `src/features/pipeline.py` defines
- `ColumnAligner` — a custom sklearn `TransformerMixin` that reindexes any
  incoming dataframe onto the exact fit-time column set, coercing numeric
  columns via `pd.to_numeric(errors="coerce")` and categorical columns to
  `object` dtype. This is what makes missing/extra/reordered/loosely-typed
  request columns non-fatal.
- a `ColumnTransformer` (median imputation for numeric, most-frequent
  imputation + `OrdinalEncoder(handle_unknown="use_encoded_value")` for
  categorical)
- the `LGBMClassifier`

all three are steps in **one** `sklearn.Pipeline`. That pipeline is pickled
once (`joblib.dump`) and logged as a single MLflow artifact. The FastAPI
service loads that exact object — there is no second implementation of
"how do I turn a raw record into model input" anywhere in the codebase.

## Why a custom pyfunc wrapper

`mlflow.sklearn`'s default pyfunc flavor calls `.predict()` on the
underlying estimator, which for a classifier returns a hard 0/1 label, not
a probability. `src/modeling/mlflow_wrapper.py::FraudModelWrapper` is a
`mlflow.pyfunc.PythonModel` that calls `.predict_proba()` internally and
returns `{fraud_probability, fraud_prediction, threshold}` — exactly what
`POST /predict` needs, with no translation layer in the API code.

A second, non-obvious reason for the wrapper: MLflow's own pyfunc layer
enforces the logged input *signature* strictly by default (rejects e.g. an
`int64` column where the signature says `double`) — which would defeat the
whole point of `ColumnAligner` tolerating loosely-typed input. The API
loads the model via `mlflow.pyfunc.load_model(...).unwrap_python_model()`
and calls the wrapper directly, bypassing that enforcement layer while
still keeping the signature logged for documentation/UI purposes.

## Module map

| Module | Responsibility |
|---|---|
| `src/config.py` | Single source of truth for paths and settings; everything else imports from here |
| `src/data/ingest.py`, `sampling.py`, `validation.py` | Load, sample, and validate raw IEEE-CIS data |
| `src/features/pipeline.py` | `ColumnAligner`, schema inference, preprocessing `ColumnTransformer` |
| `src/modeling/validation.py` | Temporal/random train-val-test split |
| `src/modeling/evaluate.py` | Metric suite |
| `src/modeling/threshold.py` | Threshold-selection strategies + cost-based evaluation |
| `src/modeling/train.py` | Orchestrates the full training run and MLflow logging |
| `src/modeling/mlflow_wrapper.py` | pyfunc wrapper around the fitted pipeline |
| `src/registry/promote.py` | candidate → champion promotion gate |
| `src/registry/compare.py` | candidate vs champion comparison report |
| `src/api/` | FastAPI service (app, schemas, dependencies, middleware, metrics) |
| `src/monitoring/drift.py` | Reference-vs-current data drift report |
| `src/utils/repro.py` | Seeding + run metadata (git/env/data fingerprint) |
