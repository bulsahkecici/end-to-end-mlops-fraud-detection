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

## Slice 2 — Drift monitoring hardening

Implemented deterministic, reference-authoritative offline drift reporting
without starting delayed-label monitoring:

- An operator-supplied contract is expected to copy the fitted model artifact's
  persisted numeric and categorical feature roles plus immutable model/run/
  optional deployment identity and stored-threshold provenance. Current data
  never defines schema. Optional deployment-state validation confirms identity,
  but the monitor does not load model metadata or independently prove that the
  supplied feature list is complete.
- Stable input, schema/config, and semantic-report SHA-256 fingerprints use
  canonical content ordering; volatile generation time is kept outside the
  semantic report identity.
- Numeric evidence uses reference-derived quantile bins, fixed unchanged for
  current data, with bounded total-variation checks and explicit handling of
  missing, invalid/non-finite, constant, all-null, and absent columns.
- Categorical evidence is capped at reference top-K plus `OTHER`, `MISSING`,
  and `UNKNOWN`; category values are represented by SHA-256 tokens rather than
  emitted as uncontrolled raw high-cardinality lists. The unsalted tokens are
  pseudonymous bounded identifiers, not secrecy or anonymization.
- Prediction probabilities and decisions are monitored only through explicitly
  configured prediction columns. Label prevalence is never called prediction
  drift. An unusable configured reference baseline fails closed; current
  prediction data with no valid comparable values is `NOT_EVALUATED` with an
  explicit availability breach rather than a healthy pass.
- Machine checks use fixed status/severity vocabularies. PASS/WARN exits zero,
  BREACH exits one, and invalid contracts/inputs/outputs exit two.
- The CLI requires all inputs, sources, windows, contract, and output path. It
  has no synthetic fallback and refuses implicit overwrite. JSON/Markdown pairs
  use a best-effort local-filesystem transaction: both are prepared first, and
  a failed second publication removes the new first output or restores its
  prior version. This does not claim distributed transaction guarantees.

Still deferred: delayed-label performance monitoring, prediction/label
persistence, streaming, alert delivery, retraining/promotion/rollback triggers,
artifact signing, broad artifact-tree integrity, MLflow major migration, MinIO
replacement, and Phase 6.

Verification on 2026-10-01 passed 37 focused drift tests, 168 unit tests, 21
integration tests, the full 189-test suite, and the 75% coverage gate at 87.49%.
Ruff, Black check, mypy, `pip check`, the exact reviewed `pip-audit` baseline,
and `git diff --check` passed. A generated JSON report passed strict parsing
with no NaN/Infinity, and its Markdown companion was manually inspected for
matching provenance, feature, prediction, and status content. Gitleaks, Trivy,
and Syft remained unavailable. Production-like Docker E2E was not rerun and
remains blocked/unverified by the obsolete Community MinIO distribution.
