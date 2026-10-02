# Project state

- **Current phase:** POST-PHASE-5 container security remediation; Phase 5
  Monitoring + Security Slices 1–3 remain implemented. Deterministic drift and
  repeatable security evidence are retained; delayed-label monitoring is deferred.
- **Merged Phase 2 commit:** `b5293d5eee0a6210658d5be1a048618e9792bc6d`
- **Merged Phase 1 PR commit:** `e51594d2f9f93832afcc046d33098e2df69bb680`
- **Phase 1 implementation commit:** `17229aff64d8ad3afb6d39f9b6651eb66dce4771`
- **Verified Phase 1 base/bootstrap commit:** `4dc6d550332b1f6106769ca2c48723d4cdef131c`
- **Default branch:** `master`
- **Next authorized work:** POST-PHASE-5 container security remediation;
  PHASE 6 remains blocked until remediation is verified

## Canonical architecture

IEEE-CIS CSV or synthetic input flows through ingestion, validation, deterministic sampling, temporal train/calibration-fit/selection-validation/promotion-evaluation/final-test splitting, a train-fitted `ColumnAligner` + `ColumnTransformer` + LightGBM pipeline with optional serialized probability calibration, an MLflow pyfunc wrapper, the Model Registry, explicit immutable deployment state, and a FastAPI service behind optional NGINX. Local-lite uses SQLite/local artifacts; production-like Compose defines Postgres and MinIO.

## Train/serve contract

Schema inference, imputation, and categorical encoding are fit only on training data. The preserved baseline ordinal-encodes bounded-cardinality categoricals and drops higher-cardinality columns; the explicit `frequency_high_cardinality` variant instead retains them through label-independent train-fitted relative-frequency maps, with unknown values mapped to zero. Optional sigmoid or isotonic calibration wraps the fitted estimator and is fit only on the calibration-fit portion of selection-validation. Training and serving share the same serialized fitted sklearn pipeline inside one MLflow model artifact. An explicit deployment action freezes the approved `champion` into append-only deployment history and atomically replaces current deployment state. The API loads only `models:/<name>/<version>` from that state at process startup, unwraps the repository pyfunc wrapper, and returns probability, binary decision, and the selection-validation threshold. Transport validation is separate from semantic validation: model feature roles come from the loaded artifact's `feature_schema` metadata, and every accepted record flows unchanged through the canonical fitted pipeline.

## Candidate/champion lifecycle

Training registers a version and assigns only `candidate`. It logs a frozen promotion-evaluation artifact and manifest containing exact-row SHA-256 identity, dataset/version identity, row count, target distribution, time boundaries, source-data fingerprint, split semantics, a per-artifact byte checksum, and a marker excluding final-test data. `src/registry/promote.py` freezes candidate/champion versions, compares semantic evaluation identity without treating independently written Parquet checksums as dataset identity, independently verifies each artifact against its own checksum, confirms the loaded rows are identical, loads each immutable version's own fitted pipeline, and rescores both on the same rows with each model's stored threshold. Absolute PR-AUC/recall and champion-regression gates use only these rescored metrics. Missing or mismatched evidence, invalid predictions/metrics, model or artifact failures, registry failures, and a moved candidate alias block promotion without changing the existing champion. A complete decision trace, including successful champion-alias movement, is logged to the candidate run. Promotion does not deploy or hot-reload the API. Deployment revalidates the immutable version/run ID and rechecks `champion` immediately before persistence; rollback restores only recorded deployment history and never moves the registry alias. API recreation is explicit after either transition.

## Evaluation strategy

The default outer split remains temporal: earliest 70% train, next 15% development pool, latest 15% final test. The development pool is split in temporal order into 7.5% model-quality selection pool and 7.5% promotion evaluation. The selection pool is split again: its earlier half is used for LightGBM early stopping and optional calibration fitting, while its later half is used for variant metrics and threshold selection. Preprocessing fits on train; promotion uses only its frozen evaluation partition. Routine training neither scores nor exposes final-test metrics; `python -m src.modeling.final_test --model-uri ...` is the explicit release-reporting path and cannot change registry state. Experiment metadata includes variant/configuration details plus exact calibration-fit, selection, source-data, and promotion identities. Synthetic final-test reports are labeled plumbing evidence.

## Known limitations

- The ordinal/drop and uncalibrated baseline remains the default because no real IEEE-CIS benchmark is available in the repository. Frequency encoding and sigmoid/isotonic calibration are implemented experiment variants but are not selected based on synthetic results.
- Drift monitoring is deterministic and reference-authoritative, with fixed
  reference-derived numeric bins, bounded categorical buckets, explicit
  prediction-output handling, provenance/fingerprints, and machine-readable
  checks. It remains an offline supplied-file workflow: there is no prediction
  log persistence, delayed-ground-truth performance pipeline, or alerting
  integration. Feature roles and provenance are operator-supplied; optional
  deployment-state validation checks identity but does not independently prove
  feature-schema completeness against the deployed artifact.
- Production-like Postgres was independently verified healthy and the aligned
  MLflow/API images build. Complete stack verification is blocked because the
  archived Community MinIO project has moved to source-only distribution and
  its pre-existing `minio/minio:latest` container reference is obsolete and
  unavailable. MinIO initialization, MLflow-over-S3, API, and NGINX therefore
  remain unverified together. Phase 4 intentionally neither builds Community
  MinIO from source nor maintains a repository-owned distribution image.
- AIStor was not substituted for Community MinIO because that would change the
  project's product and licensing assumptions. Selecting a maintained
  S3-compatible object store is a future infrastructure decision, not a Phase 4
  implementation defect; the blocker is not expected to resolve automatically.
- Container base/service references remain tag-based rather than digest-pinned.
- Synthetic runs validate plumbing only; no canonical real IEEE-CIS release metrics are recorded.
- MLflow 2.22.5 remains pinned. The 2026-10-01 `pip-audit` result contains 54
  raw findings (28 unique advisory IDs) across runtime-reachable MLflow and
  PyArrow; all listed MLflow fixes require 3.x, several much later 3.x releases
  or no available fix. Slice 1 contains MLflow to the internal production-like
  Docker network instead of attempting an unverified major migration. The
  PyArrow finding's vulnerable C++ pre-buffering API is not exposed through the
  Python bindings according to the advisory.
- A manual fail-closed security-evidence workflow now installs checksum-pinned
  Gitleaks 8.30.1, Trivy 0.69.3, and Syft 1.52.0. It scans full reachable Git
  history with redaction, builds/scans exact local API and MLflow image IDs,
  emits SPDX JSON SBOMs, and uploads evidence tied to the source commit. This
  workflow is not a required check and has not been represented as passing
  merely because its definition exists.

## CI and local verification status

CI defines Ruff, Black, mypy, unit tests, integration tests, coverage with a 75% floor, a reviewed Python dependency audit, and an isolated local-lite lifecycle job. The retained production-like validator is an explicit `workflow_dispatch` opt-in while the Community MinIO dependency is structurally blocked; ordinary required CI emits a notice and does not represent that validator as passing. Phase 4 verification on 2026-09-30 passed Ruff, Black check, mypy, 132 unit tests, 20 integration tests, and the full 152-test suite at 86.55% coverage. The focused Phase 2 promotion regression set passed 32 tests, including promotion lifecycle; the calibrated Phase 3 artifact lifecycle remains covered by that passing integration test. The isolated local-lite synthetic lifecycle/API E2E passed all 9 steps: tracking-store creation, training/registration, candidate assignment, promotion, explicit deployment, API startup, immutable readiness identity, single prediction, and batch prediction. Both Compose profiles render. The production validator built the aligned MLflow/API images, but stopped non-zero before service startup when Docker could not pull `minio/minio:latest`; its isolated project and volumes were torn down. Postgres 16 was then independently verified healthy and accepting connections, and its isolated test volume was removed. The MLflow image contains MLflow 2.22.5, SQLAlchemy 2.0.51, psycopg2-binary 2.9.10, and boto3 1.35.90. Complete production-like lifecycle verification remains externally blocked as documented above.

Phase 5 Slice 1 verification on 2026-10-01 passed the focused middleware and
logging regression set, 137 unit tests, 21 integration tests, and the full
158-test suite at 86.72% coverage. Ruff, Black check, mypy, `pip check`, the
reviewed `pip-audit` baseline, and `git diff --check` passed. Both Compose
profiles render; the base production-like rendering publishes only NGINX while
API, MLflow, MinIO, Postgres, and MinIO initialization expose no host ports.
The production validator now adds a generated temporary override that publishes
only API, MLflow, and MinIO on loopback for its host-side checks, with Postgres
still private. Semantic-validation metrics count each rejected request exactly
once, using a single allowlisted reason, `multiple`, or `other`. Docker image
builds, NGINX runtime validation, Trivy, and SBOM generation did not run because
the Docker daemon was unavailable. Production-like E2E was not rerun and
remains blocked by the obsolete Community MinIO distribution.

Phase 5 Slice 2 verification on 2026-10-01 passed 37 focused drift tests, 168
unit tests, 21 integration tests, and the full 189-test suite at 87.49%
coverage. Ruff, Black check, mypy, `pip check`, the exact reviewed `pip-audit`
baseline, and `git diff --check` passed. Manual JSON/Markdown inspection
confirmed strict standard JSON, stable semantic identity/provenance, bounded
category tokens, explicit prediction status, rollback-safe report-pair
publication, and BREACH exit behavior.
Gitleaks, Trivy, and Syft remain unavailable and were not represented as
passing. No container surface changed; production-like Docker E2E was not
rerun and remains blocked/unverified as documented above.

Phase 5 Slice 3 adds the manual security-evidence workflow, an auditable local
runner, immutable action pins/minimum token permissions, and operator response
guidance. No Dockerfile/runtime containment setting was added without daemon
compatibility evidence. On 2026-10-02, the 69-test focused security/monitoring
set, 174 unit tests, 21 integration tests, and the full 195-test suite passed.
Ruff, Black check, mypy, `pip check`, the exact 28-advisory/2-package
`pip-audit` baseline, both Compose profile renders, workflow YAML parsing, and
`git diff --check` passed. A checksum-verified Gitleaks 8.30.1 binary scanned
the full reachable Git history with 100% value redaction and found zero
secrets. The first dependency-audit attempt could not obtain valid audit JSON
inside the network sandbox and failed closed; the authorized network retry
completed and matched the baseline.

The local Docker client is installed, but `docker info` confirmed that the
Colima daemon is not running even outside the filesystem sandbox. API/MLflow
image builds, Trivy scans, image SPDX JSON SBOM generation, NGINX runtime
validation, and production-like E2E were therefore **NOT RUN** locally. Trivy
and Syft were not installed or represented as passing. The manual CI security
workflow remains pending authoritative execution after commit; workflow
definition alone is not a security PASS. Production-like E2E also remains
blocked/unverified by the obsolete Community MinIO distribution.

The final Phase 5 review found hash-seed-dependent floating-point summation in
total-variation drift. The blocker fix sorts the bucket union and uses
`math.fsum`. Real CLI subprocess regressions under `PYTHONHASHSEED=1` and `3`
verify identical complete semantic reports, input fingerprints, semantic SHA,
drift values, check/overall statuses, and exit codes. The reviewer-equivalent
categorical case now returns `0.6435643564356436`, WARN, and exit 0 under both
seeds at breach threshold `0.6435643564356437`; tests at the exact drift value
and its adjacent floating-point thresholds preserve the existing `>=` breach
rule. The existing cross-seed input-fingerprint regression remains intact.
Verification on 2026-10-02 passed 41 drift tests, all four new CLI cases with
each outer hash seed (1 and 3), 178 unit tests, 21 integration tests, and the
full 199-test suite with 87.50% coverage (75% minimum). The new regression
also rejected the original unordered implementation in an isolated subprocess.
Ruff, Black check, mypy, `pip check`, the exact 28-advisory/2-package dependency
baseline, and `git diff --check` passed. Black initially flagged the new test
formatting; formatting only that file made its check pass. No container or CI
surface changed in this fix, so Docker/runtime/security-workflow checks were
not rerun; their pending/unverified limitations above remain unchanged.

MLflow 2.22.5 is explicitly paired with SQLAlchemy 2.0.51 because its database-store code imports a compatibility pool class removed in SQLAlchemy 2.1. A clean Python 3.11 install resolved Alembic 1.20.0 without an additional constraint, passed `pip check`, and passed all 20 registry/training tests that cover the prior CI failure before the full Phase 1 verification above was rerun.


## Final Phase 5 checkpoint verification — 2026-10-02

Before the final checkpoint commit, the complete 11-file Phase 5 diff was
reviewed against HEAD `68dffa973c18eacf1da86786c477eff1c23089c5` on
`hardening/phase-5-monitoring-security`. All final checks returned exit 0:
`pytest tests/unit/test_drift.py -q` (41 passed), `pytest tests/unit -q`
(178 passed), `pytest tests/integration -q` (21 passed), `pytest -q`
(199 passed), and the verify skill's full coverage command (199 passed,
87.50% coverage against the 75% minimum). The complete-report and categorical
threshold regressions passed under each outer `PYTHONHASHSEED=1` and `3`
(4 passed per seed). Ruff and Black checked `src tests scripts`; mypy checked
`src`; `pip check`, both Compose profile renders, and `git diff --check`
passed. Tests emitted dependency deprecation/schema warnings.

The exact Slice 3 `make security-secret-scan` path ran with checksum-verified
Gitleaks 8.30.1 and returned PASS with zero findings; evidence was written
outside the repository. `make security-audit` initially exited 2 because the
sandboxed collection returned no valid audit JSON. Its authorized network
retry exited 0 and matched the accepted 28-advisory/2-package baseline.
Accepted MLflow/PyArrow findings remain accepted findings, not remediation.
API/MLflow image builds, Trivy scans, Syft SBOMs, NGINX runtime validation,
and production-like E2E were NOT RUN at this checkpoint and remain
pending/blocked as documented above. Remote workflow execution remains
pending. No Phase 6 work or original stash contents are included.

## Post-Phase-5 container security remediation — 2026-10-02

Active branch `hardening/phase-5-security-remediation` is based directly on
merged checkpoint `3764601e559d25771770ac280c58b2932e377ef4`. This work is
container/security remediation, not Phase 6; Phase 6 remains blocked until
remediation verification completes. No commit/push or stash mutation was made.

The actual artifact from remote run `36988628850`, artifact ID `11218502716`,
was downloaded outside the repository and inspected. Each image had 132
HIGH/CRITICAL findings (115 HIGH, 17 CRITICAL; 107 Debian OS, 25 Python;
66 with a reported fix, 66 without); both together contained 75 unique IDs.
Findings were not identical: API PyArrow was 17.0.0; MLflow PyArrow was 19.0.1.
Both SBOMs parsed as SPDX 2.3 (215 API packages, 204 MLflow packages), and
Gitleaks's full-history report contained zero findings. The remote workflow
failed closed because Trivy reported real vulnerabilities.

Both final runtime Dockerfiles now perform a noninteractive apt update/upgrade
and clean package indexes; API retains libgomp1. Setuptools/wheel are removed
only after dependency installation, followed by image-build `pip check`.
Static inspection found no direct tool imports in application/MLflow/LightGBM/
pandas/sklearn runtime code; MLflow's environment-metadata behavior with absent
build-tool versions is tested. This does not establish final-image compatibility.
Python 3.11.10, MLflow 2.22.5, API PyArrow 17.0.0, and network containment are
unchanged. Base digest pinning remains deferred. Local/CI security evidence
builds now use `--pull --no-cache`.

The artifact-derived exact baseline contains 44 reviewed residual identities:
21 MLflow plus one PyArrow per image, with role, ID, package, version, severity,
fixed-version snapshot, rationale, review/expiry dates, and artifact report
fingerprints. MLflow has 18 findings reporting only 3.x fixes and three without
reported fixes. PyArrow's reported fix at 23.0.1 conflicts with MLflow's `<20`
constraint. These are accepted risks, not fixed or false-positive claims;
containment reduces exposure without remediation. No OS/packaging finding is
pre-accepted. Any remaining OS findings require remediation based on fresh scans.

PASS means no HIGH/CRITICAL findings; ACCEPTED means only exact non-expired
reviewed findings remain; FAIL means policy/evidence/tool failure. ACCEPTED is
not vulnerability-free. Reviews expire on 2026-11-01. Severity/identity/fixed-
version changes, expired/future reviews, malformed/duplicate/wildcard baseline
entries, scanner failures, inconsistent scanner exit codes, image identity
failures, and invalid SBOM evidence fail closed. Stale entries fail closed until reviewed and reconciled. Any fix snapshot change
requires re-review, including newly compatible fixes. Summaries bind the baseline
SHA-256 and immutable image IDs; diagnostic artifact upload remains `always()`.

Verification commands used the repository `.venv`:

- `pytest tests/unit/test_security_evidence.py -q`: exit 0, final 59 passed.
- `pytest tests/unit -q`: exit 0, 225 passed before the final six focused cases
  were added; all added cases passed in the final focused run.
- `pytest tests/integration -q`: exit 0, 21 passed, covering training,
  model serialization/load, MLflow wrapper/registry, and API prediction.
- `pytest -q`: exit 0, 249 passed before the last three focused cases were
  added; those cases passed in the final focused run.
- Final verify coverage command
  `pytest --cov=src --cov-report=term-missing --cov-fail-under=75`: exit 0,
  all 252 final tests passed at 87.50% coverage against the 75% minimum.
- `ruff check src tests scripts`, `black --check src tests scripts`, `mypy src`,
  and `pip check`: final exit 0. Initial Ruff import/style findings were fixed.
- `make security-audit`: sandboxed attempt exited 2 (no valid audit JSON);
  network-enabled retry exited 0, matching 28 unique advisories across two
  packages using pip-audit 2.10.1. These remain reviewed dependency risks.
- A new malformed-SBOM test initially failed because its fixture still emitted
  valid SPDX; the fixture was corrected, and the final focused run passed.
- Both Compose profile renders and production-like port-containment assertions:
  exit 0. Workflow YAML parsed successfully with the `.venv` interpreter; an
  initial system-Python attempt lacked PyYAML and exited 1.
- Pure-Python artifact fingerprint/evaluation checks: exit 0. Each old report
  remains FAIL (132 findings, 22 reviewed matches, 110 unreviewed, zero stale);
  each artifact-derived MLflow/PyArrow-only subset evaluates ACCEPTED with 22
  exact matches. This is evaluator evidence, not a new scan.

`docker info` exited 1 outside the sandbox because the Colima daemon is not
running. Image rebuilds, runtime smoke, fresh Trivy scans, and new Syft SBOMs
are NOT RUN. No reduction of OS findings or compatibility of the changed final
images is claimed. Production-like E2E also retains its independent obsolete
MinIO distribution blocker; MinIO was not replaced. Remediation remains ready
for remote image verification, with OS residual review pending that evidence.

Final `git diff --check` passed (exit 0). The final scope review confirmed only
the two Dockerfiles, security Make targets/workflow, evidence runner/tests, exact
container baseline, and three documentation files changed. HEAD remains the
merged Phase 5 checkpoint and the original pre-Phase-5 stash remains intact.


## Independent remediation review and local verification — 2026-10-02

The implementation was reviewed again against original artifact `11218502716`,
which was independently downloaded using GitHub CLI. Both report SHA-256 values
match the baseline provenance; each old report still fails with 132 findings,
22 exact reviewed matches and 110 unreviewed findings. MLflow/PyArrow-only
subsets evaluate ACCEPTED with zero stale entries; this is evaluator validation,
not fresh container evidence.

The review corrected stale-baseline handling to FAIL until reconciliation,
strengthened SPDX document/package validation, and rejected malformed or
exit-inconsistent Gitleaks reports. The manual workflow now tests final API
image imports, LightGBM serialization, MLflow wrapper save/load and prediction,
and MLflow server CLI/dependency imports after build-tool removal. These use
synthetic smoke data and do not establish IEEE-CIS performance.

Actual local command results (repository `.venv`, all exit 0):

- Focused security tests: 62 passed after the final three secret-report cases.
- Unit suite: 231 passed before those final three cases; all three pass focused.
- Integration suite: 21 passed (training, serialization, wrapper, registry, API).
- `pytest -q`: 252 passed before the final three cases.
- Coverage verification: 252 passed, 87.50% coverage before the final three cases;
  final complete-suite verification follows below.
- Ruff and Black across `src tests scripts`, Mypy `src`, and `pip check`: PASS.
- `make security-audit`: PASS, 28 unique reviewed advisories across two packages.
- Both Compose profiles render, workflow YAML parses, smoke shell passes `bash -n`.
- `scripts/validate_e2e.py`: 9/9 PASS using throwaway SQLite/deployment state,
  including live localhost API prediction on synthetic data.
- `git diff --check`: PASS.

GitHub authentication works outside the network sandbox. Local `docker info`
exits 1 because Colima is not running; local builds, image smoke, Trivy and Syft
are NOT RUN. Production-like E2E remains independently blocked by the retained
MinIO distribution. No Phase 6, MLflow major migration, MinIO replacement, or
model lifecycle changes are included. The original stash object remains
`3384b5a200c73bd07a36dabb11f274280d72ca20`.

Final complete-suite coverage verification: exit 0, **255 passed**, **87.50%**
coverage (75% minimum). Final unit rerun: exit 0, **234 passed**.
