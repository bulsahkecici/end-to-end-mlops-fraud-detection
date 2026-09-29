# PHASE 3 — Model Quality

## Goal

Evaluate categorical handling, high-cardinality strategy, and probability calibration through reproducible, leakage-safe benchmarks.

## Scope

- Benchmark improved categorical and high-cardinality approaches against the preserved baseline.
- Evaluate probability calibration and its effect on quality and business cost.
- Record comparable experiment evidence and retain only demonstrated improvements.

## Non-goals

- Promotion-policy, deployment-lifecycle, monitoring, or security redesign.
- Replacing the baseline without comparable evidence.

## Required invariants

- Follow `AGENTS.md` and the `ml-evaluation` skill.
- Fit all feature and calibration components without validation/test leakage.
- Use validation for experiment choice and final test only once after the choice is frozen.
- Preserve the single canonical train/serve pipeline.

## Expected implementation surface

- Feature pipeline, model training/evaluation, experiment configuration, tests, and model-quality documentation.

## Required tests/verification

- Leakage and train/serve parity tests plus reproducible baseline-versus-experiment benchmarks.
- Required discrimination, classification, calibration, and business-cost metrics on comparable data.
- Full verification via the `verify` skill.

## Completion criteria

- The selected approach has reproducible evidence of improvement or the existing baseline is explicitly retained.
- Calibration and high-cardinality decisions are documented with dataset fingerprints and metrics.
- `docs/PROJECT_STATE.md` is updated.
