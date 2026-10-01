# Project state

- **Current phase:** PHASE 5 — Monitoring + Security, Slice 1 implemented and
  Python/Compose-config verified; container builds and NGINX runtime validation
  were not run because the local Docker daemon was unavailable, and full
  production-like E2E remains blocked by the obsolete Community MinIO
  distribution
- **Merged Phase 2 commit:** `b5293d5eee0a6210658d5be1a048618e9792bc6d`
- **Merged Phase 1 PR commit:** `e51594d2f9f93832afcc046d33098e2df69bb680`
- **Phase 1 implementation commit:** `17229aff64d8ad3afb6d39f9b6651eb66dce4771`
- **Verified Phase 1 base/bootstrap commit:** `4dc6d550332b1f6106769ca2c48723d4cdef131c`
- **Default branch:** `master`
- **Next authorized work:** PHASE 5 Slice 2 only when explicitly requested;
  PHASE 6 has not started

## Canonical architecture

IEEE-CIS CSV or synthetic input flows through ingestion, validation, deterministic sampling, temporal train/calibration-fit/selection-validation/promotion-evaluation/final-test splitting, a train-fitted `ColumnAligner` + `ColumnTransformer` + LightGBM pipeline with optional serialized probability calibration, an MLflow pyfunc wrapper, the Model Registry, explicit immutable deployment state, and a FastAPI service behind optional NGINX. Local-lite uses SQLite/local artifacts; production-like Compose defines Postgres and MinIO.

## Train/serve contract

Schema inference, imputation, and categorical encoding are fit only on training data. The preserved baseline ordinal-encodes bounded-cardinality categoricals and drops higher-cardinality columns; the explicit `frequency_high_cardinality` variant instead retains them through label-independent train-fitted relative-frequency maps, with unknown values mapped to zero. Optional sigmoid or isotonic calibration wraps the fitted estimator and is fit only on the calibration-fit portion of selection-validation. Training and serving share the same serialized fitted sklearn pipeline inside one MLflow model artifact. An explicit deployment action freezes the approved `champion` into append-only deployment history and atomically replaces current deployment state. The API loads only `models:/<name>/<version>` from that state at process startup, unwraps the repository pyfunc wrapper, and returns probability, binary decision, and the selection-validation threshold. Transport validation is separate from semantic validation: model feature roles come from the loaded artifact's `feature_schema` metadata, and every accepted record flows unchanged through the canonical fitted pipeline.

## Candidate/champion lifecycle

Training registers a version and assigns only `candidate`. It logs a frozen promotion-evaluation artifact and manifest containing exact-row SHA-256 identity, dataset/version identity, row count, target distribution, time boundaries, source-data fingerprint, split semantics, a per-artifact byte checksum, and a marker excluding final-test data. `src/registry/promote.py` freezes candidate/champion versions, compares semantic evaluation identity without treating independently written Parquet checksums as dataset identity, independently verifies each artifact against its own checksum, confirms the loaded rows are identical, loads each immutable version's own fitted pipeline, and rescores both on the same rows with each model's stored threshold. Absolute PR-AUC/recall and champion-regression gates use only these rescored metrics. Missing or mismatched evidence, invalid predictions/metrics, model or artifact failures, registry failures, and a moved candidate alias block promotion without changing the existing champion. A complete decision trace, including successful champion-alias movement, is logged to the candidate run. Promotion does not deploy or hot-reload the API. Deployment revalidates the immutable version/run ID and rechecks `champion` immediately before persistence; rollback restores only recorded deployment history and never moves the registry alias. API recreation is explicit after either transition.

## Evaluation strategy

The default outer split remains temporal: earliest 70% train, next 15% development pool, latest 15% final test. The development pool is split in temporal order into 7.5% model-quality selection pool and 7.5% promotion evaluation. The selection pool is split again: its earlier half is used for LightGBM early stopping and optional calibration fitting, while its later half is used for variant metrics and threshold selection. Preprocessing fits on train; promotion uses only its frozen evaluation partition. Routine training neither scores nor exposes final-test metrics; `python -m src.modeling.final_test --model-uri ...` is the explicit release-reporting path and cannot change registry state. Experiment metadata includes variant/configuration details plus exact calibration-fit, selection, source-data, and promotion identities. Synthetic final-test reports are labeled plumbing evidence.

## Known limitations

- The ordinal/drop and uncalibrated baseline remains the default because no real IEEE-CIS benchmark is available in the repository. Frequency encoding and sigmoid/isotonic calibration are implemented experiment variants but are not selected based on synthetic results.
- Drift monitoring uses lightweight mean, missing-rate, and category-share shifts; there is no delayed-ground-truth performance pipeline or alerting integration.
- Production-like Postgres was independently verified healthy and the aligned
  MLflow/API images build. Complete stack verification is blocked because the
  archived Community MinIO project has moved to source-only distribution and
  its pre-existing `minio/minio:latest` container reference is obsolete and
  unavailable. MinIO initialization, MLflow-over-S3, API, and NGINX therefore
  remain unverified together. Phase 4 intentionally neither builds Community
  MinIO from source nor maintains a repository-owned distribution image.
- AIStor was not substituted for Community MinIO because that would change the
  project's product and licensing assumptions. Selecting a maintained
  S3-compatible object store is a future infrastructure decision, not a Phase 4
  implementation defect; the blocker is not expected to resolve automatically.
- Container base/service references remain tag-based rather than digest-pinned.
- Synthetic runs validate plumbing only; no canonical real IEEE-CIS release metrics are recorded.
- MLflow 2.22.5 remains pinned. The 2026-10-01 `pip-audit` result contains 54
  raw findings (28 unique advisory IDs) across runtime-reachable MLflow and
  PyArrow; all listed MLflow fixes require 3.x, several much later 3.x releases
  or no available fix. Slice 1 contains MLflow to the internal production-like
  Docker network instead of attempting an unverified major migration. The
  PyArrow finding's vulnerable C++ pre-buffering API is not exposed through the
  Python bindings according to the advisory.
- Gitleaks, Trivy, and Syft were unavailable and did not run. Redacted tracked
  file and Git-history high-confidence-pattern checks found no match, but they
  are not substitutes for those dedicated secret, image, and SBOM tools.

## CI and local verification status

CI defines Ruff, Black, mypy, unit tests, integration tests, coverage with a 75% floor, a reviewed Python dependency audit, and an isolated local-lite lifecycle job. The retained production-like validator is an explicit `workflow_dispatch` opt-in while the Community MinIO dependency is structurally blocked; ordinary required CI emits a notice and does not represent that validator as passing. Phase 4 verification on 2026-09-30 passed Ruff, Black check, mypy, 132 unit tests, 20 integration tests, and the full 152-test suite at 86.55% coverage. The focused Phase 2 promotion regression set passed 32 tests, including promotion lifecycle; the calibrated Phase 3 artifact lifecycle remains covered by that passing integration test. The isolated local-lite synthetic lifecycle/API E2E passed all 9 steps: tracking-store creation, training/registration, candidate assignment, promotion, explicit deployment, API startup, immutable readiness identity, single prediction, and batch prediction. Both Compose profiles render. The production validator built the aligned MLflow/API images, but stopped non-zero before service startup when Docker could not pull `minio/minio:latest`; its isolated project and volumes were torn down. Postgres 16 was then independently verified healthy and accepting connections, and its isolated test volume was removed. The MLflow image contains MLflow 2.22.5, SQLAlchemy 2.0.51, psycopg2-binary 2.9.10, and boto3 1.35.90. Complete production-like lifecycle verification remains externally blocked as documented above.

Phase 5 Slice 1 verification on 2026-10-01 passed the focused middleware and
logging regression set, 137 unit tests, 21 integration tests, and the full
158-test suite at 86.72% coverage. Ruff, Black check, mypy, `pip check`, the
reviewed `pip-audit` baseline, and `git diff --check` passed. Both Compose
profiles render; the base production-like rendering publishes only NGINX while
API, MLflow, MinIO, Postgres, and MinIO initialization expose no host ports.
The production validator now adds a generated temporary override that publishes
only API, MLflow, and MinIO on loopback for its host-side checks, with Postgres
still private. Semantic-validation metrics count each rejected request exactly
once, using a single allowlisted reason, `multiple`, or `other`. Docker image
builds, NGINX runtime validation, Trivy, and SBOM generation did not run because
the Docker daemon was unavailable. Production-like E2E was not rerun and
remains blocked by the obsolete Community MinIO distribution.

MLflow 2.22.5 is explicitly paired with SQLAlchemy 2.0.51 because its database-store code imports a compatibility pool class removed in SQLAlchemy 2.1. A clean Python 3.11 install resolved Alembic 1.20.0 without an additional constraint, passed `pip check`, and passed all 20 registry/training tests that cover the prior CI failure before the full Phase 1 verification above was rerun.
