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
