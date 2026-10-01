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

## Slice 1 — Security containment and observability correctness

Implemented on `hardening/phase-5-monitoring-security` without starting the
broader drift/delayed-label work:

- HTTP metric and access-log endpoints use configured route templates, with
  all unknown paths collapsed to `unmatched`.
- API-key checks use constant-time comparison, preserve the intentional
  `/health`, `/ready`, and `/metrics` exemptions, and allow genuine CORS
  preflight to reach CORS validation.
- Low-cardinality counters cover authentication, body-size rejection,
  semantic-validation rejection, readiness, and model-load failures. Each
  semantically rejected request increments exactly once with one allowlisted
  reason, `multiple`, or `other`.
- Deployment identity/action are emitted by the JSON formatter, while API
  exception logs retain only the exception class rather than messages or
  tracebacks that could expose paths or connection details.
- Local-lite host ports bind to loopback. Production-like publishes only
  NGINX; API, MLflow, MinIO API, and MinIO console remain internal. The retained
  host-side production validator creates a temporary Compose override that
  publishes only API, MLflow, and MinIO on loopback and removes it with the
  validator's isolated state.
- MLflow remains at 2.22.5. The 2026-10-01 audit found no compatible 2.x
  remediation, so an unverified 3.x migration is rejected in favor of network
  containment and an explicit reviewed-risk baseline.
- `make security-audit` and CI run `pip check` plus a pinned `pip-audit` whose
  exact baseline fails on finding drift, newly available same-major fixes, or
  collection failure.

Still outside this slice: delayed-label monitoring, persistence, major drift
changes, signing/full artifact hashing, object-store replacement, distributed
tracing, and multi-worker Prometheus.

Verification on 2026-10-01 passed 137 unit tests, 21 integration tests, the
full 158-test suite at 86.72% coverage, Ruff, Black check, mypy, `pip check`,
the exact reviewed `pip-audit` baseline, both Compose profile renders, and
`git diff --check`. Static validator-config verification confirmed that its
temporary API, MLflow, and MinIO publications bind only to `127.0.0.1`, while
Postgres remains unexposed. Docker image builds and NGINX runtime validation
could not run because the Docker daemon was unavailable. Gitleaks, Trivy, and
Syft were not installed and did not run. Production-like E2E remains blocked
by the obsolete Community MinIO distribution documented in project state.
