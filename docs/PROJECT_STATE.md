# Project state

- **Current phase:** PHASE 2 — Trustworthy Promotion
- **Merged Phase 1 PR commit:** `e51594d2f9f93832afcc046d33098e2df69bb680`
- **Phase 1 implementation commit:** `17229aff64d8ad3afb6d39f9b6651eb66dce4771`
- **Verified Phase 1 base/bootstrap commit:** `4dc6d550332b1f6106769ca2c48723d4cdef131c`
- **Default branch:** `master`
- **Next approved phase:** PHASE 3 — Model Quality

## Canonical architecture

IEEE-CIS CSV or synthetic input flows through ingestion, validation, deterministic sampling, temporal train/selection-validation/promotion-evaluation/final-test splitting, a train-fitted `ColumnAligner` + `ColumnTransformer` + LightGBM pipeline, an MLflow pyfunc wrapper, the Model Registry, and a FastAPI service behind optional NGINX. Local-lite uses SQLite/local artifacts; production-like Compose defines Postgres and MinIO.

## Train/serve contract

Schema inference, imputation, and categorical encoding are fit only on training data. Training and serving share the same serialized fitted sklearn pipeline inside one MLflow model artifact. The API loads the registry's `champion` alias at startup, unwraps the repository pyfunc wrapper, and returns probability, binary decision, and the validation-selected threshold. Transport validation is separate from semantic validation: model feature roles come from the loaded artifact's `feature_schema` metadata, and every accepted record flows unchanged through the canonical fitted pipeline.

## Candidate/champion lifecycle

Training registers a version and assigns only `candidate`. It logs a frozen promotion-evaluation artifact and manifest containing exact-row SHA-256 identity, dataset/version identity, row count, target distribution, time boundaries, source-data fingerprint, split semantics, a per-artifact byte checksum, and a marker excluding final-test data. `src/registry/promote.py` freezes candidate/champion versions, compares semantic evaluation identity without treating independently written Parquet checksums as dataset identity, independently verifies each artifact against its own checksum, confirms the loaded rows are identical, loads each immutable version's own fitted pipeline, and rescores both on the same rows with each model's stored threshold. Absolute PR-AUC/recall and champion-regression gates use only these rescored metrics. Missing or mismatched evidence, invalid predictions/metrics, model or artifact failures, registry failures, and a moved candidate alias block promotion without changing the existing champion. A complete decision trace, including successful champion-alias movement, is logged to the candidate run. Promotion does not deploy or hot-reload the API.

## Evaluation strategy

The default outer split remains temporal: earliest 70% train, next 15% development pool, latest 15% final test. The development pool is split in temporal order into 7.5% selection-validation and 7.5% promotion evaluation. Preprocessing fits on train; early stopping and threshold selection use only selection-validation; promotion uses only its frozen evaluation partition; test remains final-reporting-only. Promotion reports threshold-independent PR-AUC, ROC-AUC, Brier score, and log loss separately from threshold-dependent precision, recall, F1, confusion counts, and expected cost, using the configured false-negative/false-positive costs.

## Known limitations

- Categorical handling is ordinal and drops very high-cardinality columns.
- Drift monitoring uses lightweight mean, missing-rate, and category-share shifts; there is no delayed-ground-truth performance pipeline or alerting integration.
- The production-like Postgres/MinIO stack is defined but was not exercised during Phase 1.
- Synthetic runs validate plumbing only; no canonical real IEEE-CIS release metrics are recorded.

## CI and local verification status

CI defines Ruff, Black, mypy, unit tests, integration tests, coverage with a 75% floor, local-lite Docker build/smoke checks, and a synthetic E2E job. Phase 2 review verification on 2026-09-30 passed Ruff, Black check, mypy, 107 unit tests, 15 integration tests, and the full 122-test suite at 87.48% coverage. The focused promotion-evaluation, registry, and lifecycle checks passed 31 tests. The isolated synthetic lifecycle/API smoke passed all 8 steps, including frozen-evaluation promotion, real Uvicorn startup, readiness, and single/batch prediction. Synthetic results validate plumbing only and are not presented as IEEE-CIS performance.

MLflow 2.22.5 is explicitly paired with SQLAlchemy 2.0.51 because its database-store code imports a compatibility pool class removed in SQLAlchemy 2.1. A clean Python 3.11 install resolved Alembic 1.20.0 without an additional constraint, passed `pip check`, and passed all 20 registry/training tests that cover the prior CI failure before the full Phase 1 verification above was rerun.
