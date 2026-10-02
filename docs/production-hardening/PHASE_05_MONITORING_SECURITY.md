# PHASE 5 — Monitoring + Security

## Goal

Strengthen distribution monitoring and establish repeatable dependency,
container, SBOM, and secret auditing, with operational guidance that does not
overstate deployed alert coverage.

## Scope

- Add stronger distribution drift evidence. Delayed-ground-truth performance
  monitoring was evaluated and explicitly deferred because this repository has
  no production prediction/label persistence or join contract.
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

- Deterministic drift tests, version/provenance tests, failure-path tests, and
  an explicit boundary for the deferred delayed-label pipeline.
- Audits for Python dependencies, images, SBOM availability, and secrets, with tool gaps recorded.
- Full relevant checks via `verify` and `security-audit` skills.

## Completion criteria

- Drift can be computed with traceable inputs and actionable output;
  delayed-ground-truth performance remains an honest deferred scope decision,
  not a readiness claim.
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

## Slice 3 — Security evidence and phase-wide closure

Implemented a repeatable, bounded evidence path without starting Phase 6 or
inventing unavailable production telemetry:

- The manual `Security Evidence` GitHub Actions workflow checks out full Git
  history, installs checksum-pinned Gitleaks 8.30.1, Trivy 0.69.3, and Syft
  1.52.0 binaries, and fails closed on missing tools, version mismatch,
  malformed evidence, scan failure, or findings. It is deliberately manual so
  unknown image findings do not create an unreviewed permanently-red required
  check; until executed, its outcome is pending rather than PASS.
- Gitleaks scans reachable committed history with full secret-value redaction.
  Real findings stop the workflow. Scanner failure and findings remain distinct
  outcomes, and evidence/logging never intentionally prints the detected value.
- Both API and MLflow images are built locally in the workflow. Trivy scans the
  immutable local image IDs for HIGH/CRITICAL vulnerabilities without a broad
  ignore rule, while Syft emits SPDX JSON SBOMs for those same IDs. A summary
  binds tool versions, scan policy, source commit, tags, image IDs, reports, and
  SBOM filenames. No registry push is needed.
- Existing and new GitHub-maintained actions are pinned to immutable full SHAs;
  checkout credentials are not persisted and workflow permissions are limited
  to repository-content read access. No tag-based or third-party action remains.
- Operator guidance now covers authentication failures, request-body rejection,
  semantic validation, readiness/model-load failures, drift BREACH/error, and
  deployment identity changes. These are proposed triggers and response steps,
  not a claim of wired alert delivery.
- The existing exact Python advisory baseline is retained. MLflow remains at
  2.22.5: its reviewed 3.x-only/no-fix advisory set is contained but not
  eliminated, and this slice does not guess at a major migration or container
  runtime hardening without executable compatibility evidence.

Phase-wide scope is now closed around delivered HTTP metrics/logging and
network containment (Slice 1), deterministic reference-authoritative offline
drift evidence (Slice 2), and reproducible security-evidence tooling plus
operator guidance (Slice 3). Delayed-label performance, prediction/label
persistence and joining, automatic alert delivery, automatic retraining/
promotion/rollback, artifact signing, remote-registry publication, MLflow 3,
MinIO replacement, Phase 6 release work, and speculative container-runtime
changes remain outside Phase 5.

Final verification facts are recorded in `docs/PROJECT_STATE.md`; tool or
Docker limitations are reported as NOT RUN rather than inferred successes.

Local verification on 2026-10-02 passed the 69-test focused security/monitoring
set, 174 unit tests, 21 integration tests, the full 195-test suite, Ruff, Black
check, mypy, `pip check`, the exact reviewed dependency baseline, both Compose
profile renders, workflow YAML parsing, and `git diff --check`. A downloaded
Gitleaks 8.30.1 archive matched its pinned SHA-256; the tool then scanned full
reachable Git history with 100% redaction and returned zero findings. Docker
daemon access was unavailable, so local API/MLflow builds, Trivy scans, image
SBOM generation, NGINX runtime checks, and production-like E2E were NOT RUN.
The manual CI evidence workflow remains pending execution, and production-like
E2E remains independently blocked by the obsolete Community MinIO image.
