# Project state

- **Current phase:** BOOTSTRAP
- **Current verified commit:** `32b503eb0f62a29dfa18c6c07f46132cdecd1349` (baseline `HEAD`; bootstrap files and pre-existing Mac compatibility changes are uncommitted)
- **Default branch:** `master`
- **Next approved phase:** PHASE 1 — Inference Safety

## Canonical architecture

IEEE-CIS CSV or synthetic input flows through ingestion, validation, deterministic sampling, temporal train/validation/test splitting, a train-fitted `ColumnAligner` + `ColumnTransformer` + LightGBM pipeline, an MLflow pyfunc wrapper, the Model Registry, and a FastAPI service behind optional NGINX. Local-lite uses SQLite/local artifacts; production-like Compose defines Postgres and MinIO.

## Train/serve contract

Schema inference, imputation, and categorical encoding are fit only on training data. Training and serving share the same serialized fitted sklearn pipeline inside one MLflow model artifact. The API loads the registry's `champion` alias at startup, unwraps the repository pyfunc wrapper, and returns probability, binary decision, and the validation-selected threshold.

## Candidate/champion lifecycle

Training registers a version and assigns `candidate`. `src/registry/promote.py` separately checks loadability, signature, smoke prediction, minimum validation PR-AUC/recall, and regression versus logged champion validation metrics before assigning `champion`. Promotion currently does not deploy or hot-reload the API.

## Evaluation strategy

The default split is temporal: earliest 70% train, next 15% validation, latest 15% final test. Preprocessing fits on train; early stopping and threshold selection use validation; test is intended for final reporting only. Logged metrics include PR-AUC, ROC-AUC, precision, recall, F1, Brier score, log loss, fraud rate, and confusion counts.

## Known limitations

- Inference currently accepts arbitrary record keys and can impute records with little usable semantic content; Phase 1 owns tightening this contract.
- Candidate/champion comparison uses each run's logged validation metrics rather than rescoring both models on one frozen canonical evaluation dataset.
- Categorical handling is ordinal and drops very high-cardinality columns.
- Drift monitoring uses lightweight mean, missing-rate, and category-share shifts; there is no delayed-ground-truth performance pipeline or alerting integration.
- The production-like Postgres/MinIO stack is defined but was not exercised in this docs-only bootstrap.
- Synthetic runs validate plumbing only; no canonical real IEEE-CIS release metrics are recorded.

## CI and local verification status

CI defines Ruff, Black, mypy, unit tests, integration tests, coverage with a 75% floor, local-lite Docker build/smoke checks, and a synthetic E2E job. Before this bootstrap, the local Mac baseline completed 70 tests plus Ruff, Black, mypy, local-lite Docker health/prediction, and isolated synthetic E2E successfully. Remote GitHub Actions status was not queried. Bootstrap-specific verification is recorded in the task handoff; no runtime behavior is changed by the bootstrap files.
