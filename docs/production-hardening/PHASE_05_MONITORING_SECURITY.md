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

## Post-Phase-5 container security remediation — 2026-10-02

The actual manual workflow run `36988628850` at merged checkpoint
`3764601e559d25771770ac280c58b2932e377ef4` failed closed on real container
findings. Artifact `11218502716` contains 132 HIGH/CRITICAL findings per image
(115 HIGH, 17 CRITICAL; 107 Debian, 25 Python; 66 with fixes, 66 without).
There are 75 unique vulnerability IDs across both images. API PyArrow is
17.0.0; MLflow's compatible transitive resolution was 19.0.1.

The first remediation runtime stages refreshed apt indexes and perform a noninteractive
Debian package upgrade, preserving API's libgomp1 and cleaning apt lists.
The original Python 3.11.10 Bookworm base was insufficient: fresh upgrades
left 63 unaccepted OS findings per image (run `36995248203`). Both Dockerfiles
then used official Python 3.11.17 slim Trixie, consistently pinned to manifest
index `sha256:45037981b62b34b44602584fccbc4d884d5f7dc92c7ee86bb38a698a79fe1e51`.
The registry manifest was inspected directly. This scoped base refresh retains
Python 3.11 and every application dependency pin; fresh evidence is still required.
Setuptools and wheel are removed after dependency installation, followed by
`pip check`. They are unpinned packaging tools, not application dependencies;
inspection found no direct runtime imports in the application, MLflow,
LightGBM, pandas, or sklearn. The canonical API loads its fitted model in the
existing process, and the MLflow container runs the tracking server. This is
not a guarantee for arbitrary MLflow model-environment construction or build
commands. Runtime compatibility still requires fresh container verification.

`security/container_vulnerability_baseline.json` contains 44 exact reviewed
MLflow/PyArrow entries generated from the downloaded artifact, with source
revision, run/artifact IDs, and report SHA-256 fingerprints. No OS finding or
packaging-tool finding is pre-accepted. Per image, MLflow 2.22.5 has 21 findings:
18 report only 3.x fixes and three report no fix (CVE-2026-0545,
CVE-2024-37059, CVE-2025-15381). PyArrow CVE-2026-25087 reports a fix at 23.0.1,
outside MLflow 2.22.5's `pyarrow>=4,<20` constraint. These remain accepted risk,
not fixed findings or asserted false positives. Network containment is unchanged
and reduces exposure without remediation.

The image evidence command defaults to the checked-in baseline; CI passes it
explicitly and builds with `--pull --no-cache`. PASS means no HIGH/CRITICAL
findings. ACCEPTED means every finding matches a current exact review. FAIL
means unreviewed/changed findings, expired/future reviews, scanner/tool errors,
malformed reports/baseline, mismatched image identity, inconsistent scanner exit
codes, or SBOM failure. PASS and ACCEPTED exit zero; FAIL exits nonzero.
ACCEPTED does not mean vulnerability-free.

Identity includes image role, vulnerability ID, package, installed version, and
severity. Wildcards and duplicate identities are rejected. Any change to the
reported fixed-version snapshot fails for re-review, including newly compatible
fixes; compatibility is not guessed from a version string. Reviews expire on
2026-11-01 (expiry day is already expired), and dates use UTC. Stale entries are
explicitly listed in each image's `baseline_evaluation` and fail closed until
reviewed and reconciled. Summary records include a baseline SHA-256 and deterministic, order-independent issue
identities. Diagnostic artifact upload remains `always()`.

Fresh builds, pinned Trivy scans, and Syft SBOMs remain required remotely.
Any remaining OS findings require remediation; only the reviewed
MLflow/PyArrow residual risk is eligible for final acceptance. The old reports deliberately remain FAIL, with
22 matched and 110 unreviewed findings per image. No OS reduction is claimed.
Phase 6 remains blocked until this remediation is verified; MLflow migration,
MinIO replacement, drift, authentication, and model lifecycle changes are excluded.


## Selected production runtime redesign

The intermediate Trixie evidence still had 44 unaccepted OS findings across
17 packages per image. The final design uses public digest-pinned Wolfi,
upstream Python 3.11.17, an exact 38-package OS lock, matched venv builder/runtime
and frozen application Python constraints. No bespoke distro compilation,
Debian unstable mixing, custom rootfs collector, or inference change remains.
The MLflow image's native model stack is aligned with the unchanged API stack
for the requested cross-image artifact compatibility. No OS finding is accepted.

Final layers run pip check then uninstall pip, while builders retain installation
tools. Real containers prove imports/native dependencies, certificate trust,
timezone, serialization, cross-image pyfunc, live MLflow health and the non-root
API training/promotion/deployment/readiness/prediction lifecycle. Synthetic
checks establish plumbing only. Trivy must recognize the OS and cover every
Syft OS identity; the gate now rejects the independently discovered unknown-OS
false green from an abandoned custom-rootfs comparison.

Fully locked branch run 37001197837 / artifact 11223908196 passed every runtime
and security step. Each image has 22 exact reviewed Python findings (14 HIGH,
8 CRITICAL), zero OS/unreviewed/stale findings and zero changed fix snapshots;
38/38 OS coverage passes. The reviewed baseline is unchanged. Final merge/master
CI and evidence status are recorded in PROJECT_STATE.md; Phase 6 is not started.
The original MinIO-dependent production-stack limitation remains independent.
