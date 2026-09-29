# PHASE 1 — Inference Safety

## Goal

Tighten semantic inference validation so optional missing data remains supported while malformed or unusable requests fail clearly, without breaking train/serve parity.

## Scope

- Define usable-record semantics and explicit rejection behavior.
- Validate batch and record content at the inference boundary.
- Preserve the canonical fitted preprocessing path for accepted requests.
- Update API contract documentation and tests.

## Non-goals

- Model architecture, training quality, evaluation, threshold, promotion, registry, or deployment changes.
- Broadening accepted inference inputs.

## Required invariants

- Follow `AGENTS.md`; especially preserve train-only fitting and one train/serve preprocessing path.
- Validation changes must be explicit, tested, and fail safely without exposing internal errors.
- Existing valid requests must remain traceable to documented contract decisions.

## Expected implementation surface

- `src/api/` request schemas and prediction boundary.
- Focused API unit/integration tests.
- `docs/api.md` and relevant README contract text.

## Required tests/verification

- Positive and negative semantic-validation tests, batch-boundary tests, and train/serve parity regression tests.
- Full Python quality, unit, integration, and coverage checks via the `verify` skill.
- API/E2E smoke verification because serving behavior changes.

## Completion criteria

- Optional missing values and malformed/unusable inputs are unambiguously distinguished.
- Rejection statuses/messages and accepted-input behavior are documented and tested.
- No model-quality or promotion behavior changed, and `docs/PROJECT_STATE.md` is updated.
