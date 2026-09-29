# PHASE 6 — Canonical IEEE-CIS Release

## Goal

Produce a traceable canonical release based on the real IEEE-CIS dataset and publish clearly labeled, reproducible model evidence.

## Scope

- Run and record one canonical real IEEE-CIS experiment under the hardened lifecycle.
- Publish dataset/run/model/evaluation/deployment traceability and real metrics.
- Update the model card, architecture documentation, and recruiter-first README.
- Keep synthetic results explicitly labeled as smoke/test evidence only.

## Non-goals

- Representing synthetic or unavailable data as real IEEE-CIS results.
- Unscoped model, promotion, deployment, monitoring, or security redesign.

## Required invariants

- Follow `AGENTS.md` and all relevant evaluation, registry, deployment, and security skills.
- Preserve data licensing/access constraints and never commit restricted raw data.
- Use the final test split only for the frozen canonical model's final report.

## Expected implementation surface

- Canonical experiment configuration and artifacts, release evidence, model card, architecture docs, README, and reproducibility documentation.

## Required tests/verification

- Verify dataset fingerprints, split boundaries, run/model/artifact identities, metric provenance, served version, and documentation consistency.
- Run the full repository, production-like E2E, and relevant security verification workflows.

## Completion criteria

- A reproducible real-data run has traceable metrics and artifacts tied to a canonical model version and commit.
- Model card, architecture, and README clearly separate real IEEE-CIS evidence from synthetic smoke results.
- All required checks pass and `docs/PROJECT_STATE.md` records the canonical release state.
