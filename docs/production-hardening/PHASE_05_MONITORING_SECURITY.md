# PHASE 5 — Monitoring + Security

## Goal

Strengthen distribution/performance monitoring and establish repeatable dependency, container, SBOM, and secret auditing.

## Scope

- Add stronger distribution drift evidence and delayed-ground-truth performance monitoring.
- Define actionable monitoring outputs and trace them to model/data versions.
- Run and document dependency, container, SBOM, and secret-exposure checks.
- Remediate findings only through compatible, tested changes.

## Non-goals

- Blind major dependency upgrades.
- Model architecture or promotion-policy redesign.
- Claiming live operational coverage without deployed telemetry.

## Required invariants

- Follow `AGENTS.md`, `ml-evaluation`, and `security-audit` skills.
- Monitoring data and labels must have explicit windows, versions, and provenance.
- Security findings must remain distinguishable from verified remediations and accepted risks.

## Expected implementation surface

- Monitoring modules/tests/docs, metrics or reporting configuration, dependency/container configuration, and CI security checks.

## Required tests/verification

- Deterministic drift and delayed-label metric tests, version/provenance tests, and failure-path tests.
- Audits for Python dependencies, images, SBOM availability, and secrets, with tool gaps recorded.
- Full relevant checks via `verify` and `security-audit` skills.

## Completion criteria

- Drift and delayed-ground-truth performance can be computed with traceable inputs and actionable output.
- Security findings and compatible remediation status are documented and reproducible.
- `docs/PROJECT_STATE.md` is updated.
