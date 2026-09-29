# PHASE 2 — Trustworthy Promotion

## Goal

Base candidate/champion promotion on comparable rescoring against the same frozen canonical evaluation dataset.

## Scope

- Introduce canonical evaluation dataset identity and reproducible fingerprints.
- Rescore candidate and champion on identical evaluation rows and labels.
- Make promotion decisions and reports from those comparable results.
- Preserve registry traceability and fail-closed behavior.

## Non-goals

- Model architecture, feature engineering, categorical strategy, calibration, or deployment changes.
- Using final test data for promotion.

## Required invariants

- Follow `AGENTS.md` and the `ml-evaluation` and `mlflow-registry` skills.
- The promotion dataset is distinct from the final test split and immutable for a comparison.
- Training never directly promotes; promotion and deployment remain separate.

## Expected implementation surface

- Registry comparison/promotion modules and evaluation support.
- Dataset identity/fingerprint metadata and configuration.
- Promotion unit/integration tests and lifecycle documentation.

## Required tests/verification

- Tests for matching/mismatching fingerprints, absent evidence, rescoring parity, gate pass/fail, idempotence, and preserved aliases on failure.
- Full Python and registry/E2E checks via the `verify` skill.

## Completion criteria

- Every promotion decision identifies one canonical evaluation version and comparable result set for both models.
- Missing or mismatched evaluation evidence blocks promotion.
- Decision artifacts are traceable and `docs/PROJECT_STATE.md` is updated.
