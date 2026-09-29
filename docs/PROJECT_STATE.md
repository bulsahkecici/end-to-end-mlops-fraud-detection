# Project state

- **Current phase:** PHASE 1 — Inference Safety
- **Phase 1 implementation commit:** `17229aff64d8ad3afb6d39f9b6651eb66dce4771`
- **Verified Phase 1 base/bootstrap commit:** `4dc6d550332b1f6106769ca2c48723d4cdef131c`
- **Default branch:** `master`
- **Next approved phase:** PHASE 2 — Trustworthy Promotion

## Canonical architecture

IEEE-CIS CSV or synthetic input flows through ingestion, validation, deterministic sampling, temporal train/validation/test splitting, a train-fitted `ColumnAligner` + `ColumnTransformer` + LightGBM pipeline, an MLflow pyfunc wrapper, the Model Registry, and a FastAPI service behind optional NGINX. Local-lite uses SQLite/local artifacts; production-like Compose defines Postgres and MinIO.

## Train/serve contract

Schema inference, imputation, and categorical encoding are fit only on training data. Training and serving share the same serialized fitted sklearn pipeline inside one MLflow model artifact. The API loads the registry's `champion` alias at startup, unwraps the repository pyfunc wrapper, and returns probability, binary decision, and the validation-selected threshold. Transport validation is separate from semantic validation: model feature roles come from the loaded artifact's `feature_schema` metadata, and every accepted record flows unchanged through the canonical fitted pipeline.

## Candidate/champion lifecycle

Training registers a version and assigns `candidate`. `src/registry/promote.py` separately checks loadability, signature, smoke prediction, minimum validation PR-AUC/recall, and regression versus logged champion validation metrics before assigning `champion`. Promotion currently does not deploy or hot-reload the API.

## Evaluation strategy

The default split is temporal: earliest 70% train, next 15% validation, latest 15% final test. Preprocessing fits on train; early stopping and threshold selection use validation; test is intended for final reporting only. Logged metrics include PR-AUC, ROC-AUC, precision, recall, F1, Brier score, log loss, fraud rate, and confusion counts.

## Known limitations

- Candidate/champion comparison uses each run's logged validation metrics rather than rescoring both models on one frozen canonical evaluation dataset.
- Categorical handling is ordinal and drops very high-cardinality columns.
- Drift monitoring uses lightweight mean, missing-rate, and category-share shifts; there is no delayed-ground-truth performance pipeline or alerting integration.
- The production-like Postgres/MinIO stack is defined but was not exercised during Phase 1.
- Synthetic runs validate plumbing only; no canonical real IEEE-CIS release metrics are recorded.

## CI and local verification status

CI defines Ruff, Black, mypy, unit tests, integration tests, coverage with a 75% floor, local-lite Docker build/smoke checks, and a synthetic E2E job. Phase 1 review verification on 2026-09-30 passed Ruff, Black check, mypy, 84 unit tests (including 5 focused bounded-body tests), 14 integration tests (including the 2-test train/serve parity regression), and the full 98-test suite at 89.89% coverage. The isolated synthetic lifecycle/API smoke passed all 8 steps, including training, registration, promotion, real Uvicorn startup, readiness, and single/batch prediction. Remote GitHub Actions status was not queried.
