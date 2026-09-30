# PHASE 4 — Deployment Lifecycle

## Goal

Establish a verified production-like deployment lifecycle across Postgres, MinIO, MLflow, API, and NGINX with explicit deployment and rollback traceability.

## Scope

- Validate the complete production-like stack end to end.
- Separate registry promotion from deployment/reload state.
- Record deployed versions and provide a verified rollback path.
- Verify model artifact availability and served-version identity.

## Non-goals

- Model architecture, training-quality, or promotion-comparison redesign.
- Real production infrastructure provisioning.

## Required invariants

- Follow `AGENTS.md`, `mlflow-registry`, and `docker-e2e` skills.
- Promotion alone must not be represented as deployment.
- Rollback must preserve traceability and never delete registry history.

## Expected implementation surface

- Compose/deployment configuration, registry/deployment coordination, API model lifecycle, operational scripts, tests, and deployment docs.

## Required tests/verification

- Production-like health, readiness, artifact, prediction, deployed-version, promotion/deployment separation, rollback, and clean-teardown checks.
- Full relevant checks via `verify` and `docker-e2e` skills.

## Completion criteria

- A candidate can be promoted, explicitly deployed, observed by version, and rolled back in the isolated production-like stack.
- Postgres, MinIO, MLflow, API, and NGINX pass E2E validation.
- `docs/PROJECT_STATE.md` is updated.

## Implemented lifecycle

- `python -m src.deployment.lifecycle deploy` resolves the current approved
  `champion`, validates its immutable registry version/run ID and artifact,
  rechecks the alias immediately before persistence, and records the target.
- Current state is atomically replaced. Every uniquely identified transition is
  written to append-only history using exclusive creation; ID collisions fail
  without overwriting an event.
- `rollback` validates and restores only the immediately previous recorded
  deployment. It does not mutate the MLflow `champion` alias.
- API startup fails closed without valid state or a loadable matching artifact,
  loads only `models:/<name>/<version>`, and stays pinned for the process
  lifetime. Deploy/rollback requires explicit API recreation.
- `/ready` reports only the model name, immutable version, version source, run
  ID, and deployment timestamp. Prediction responses retain their prior shape.
- API containers use one Uvicorn worker because Prometheus metrics are
  process-local.

## Phase disposition

- **Implementation complete:** YES
- **Local / lifecycle verification:** PASS
- **Full production-like E2E:** BLOCKED BY UPSTREAM COMMUNITY MINIO DISTRIBUTION
- **Ready to commit Phase 4:** YES
- **Ready to claim full production-like verification:** NO

## Verification status (2026-09-30)

- Ruff, Black check, and mypy passed.
- Focused deployment tests passed 17/17; unit tests passed 132/132;
  integration tests passed 20/20.
- The full suite passed 152/152 with 86.55% coverage (75% required).
- Focused Phase 2 promotion regressions passed 32/32, including the Phase 3
  sigmoid-calibrated artifact lifecycle.
- Local-lite synthetic lifecycle/API validation passed 9/9 through explicit
  deployment, immutable readiness identity, and prediction.
- Local-lite and production-like Compose configurations render successfully.
- The repository-owned MLflow and API images build. The MLflow image was
  inspected with exact MLflow 2.22.5, SQLAlchemy 2.0.51,
  psycopg2-binary 2.9.10, and boto3 1.35.90 packages.
- An isolated Postgres 16 service became healthy and accepted connections; its
  test container, network, and volume were removed afterward.

## External production-like blocker

The complete isolated production validator exits non-zero before services start
because the archived Community MinIO project has moved to source-only
distribution and the pre-existing `minio/minio:latest` container reference is
obsolete/unavailable (`pull access denied ... repository does not exist or may
require 'docker login'`). The validator then runs `down -v --remove-orphans`.
This is a structural upstream-obsolescence blocker, not a transient outage.
Phase 4 deliberately retains the repository's existing Compose intent instead
of building Community MinIO from source or adding a repository-maintained
distribution image.

AIStor was intentionally not substituted: doing so would change the project's
product and enterprise/evaluation licensing assumptions. Selecting a maintained
S3-compatible object-store dependency is a future infrastructure decision, not
a Phase 4 implementation defect, and the blocker is not expected to resolve
automatically. The production-like validator remains available as an explicit
manual CI opt-in; normal required CI reports the blocker without claiming PASS.

Consequently, MinIO health and bucket initialization, MLflow with Postgres+S3,
explicit deployment into the Compose API, NGINX health/prediction/rate-limit
routing, and Compose rollback remain unverified as one production-like stack.
This is recorded as an external dependency-availability blocker, not a stack
PASS. Service/base image tags also remain non-digest-pinned.
