# Project state

- **Current phase:** Final documentation/portfolio release audit in progress;
  Phase 6 canonical real IEEE-CIS release COMPLETE under the
  explicitly authorized external-infrastructure exception; production-like E2E
  remains NOT VERIFIED. Phase 5 security remediation remains verified.
- **Merged Phase 2 commit:** `b5293d5eee0a6210658d5be1a048618e9792bc6d`
- **Merged Phase 1 PR commit:** `e51594d2f9f93832afcc046d33098e2df69bb680`
- **Phase 1 implementation commit:** `17229aff64d8ad3afb6d39f9b6651eb66dce4771`
- **Verified Phase 1 base/bootstrap commit:** `4dc6d550332b1f6106769ca2c48723d4cdef131c`
- **Default branch:** `master`
- **Next required action:** Final-audit PR CI/merge and localized portfolio verification. No model or infrastructure redesign is started.

Dated records below are historical checkpoints; later verified records supersede
earlier pending/blocker statements. See [final audit](final-audit.md) for the current
release presentation audit and actual local command results.

## Canonical architecture

IEEE-CIS CSV or synthetic input flows through ingestion, validation, deterministic sampling, temporal train/calibration-fit/selection-validation/promotion-evaluation/final-test splitting, a train-fitted `ColumnAligner` + `ColumnTransformer` + LightGBM pipeline with optional serialized probability calibration, an MLflow pyfunc wrapper, the Model Registry, explicit immutable deployment state, and a FastAPI service behind optional NGINX. Local-lite uses SQLite/local artifacts; production-like Compose defines Postgres and MinIO.

## Train/serve contract

Schema inference, imputation, and categorical encoding are fit only on training data. The preserved baseline ordinal-encodes bounded-cardinality categoricals and drops higher-cardinality columns; the explicit `frequency_high_cardinality` variant instead retains them through label-independent train-fitted relative-frequency maps, with unknown values mapped to zero. Optional sigmoid or isotonic calibration wraps the fitted estimator and is fit only on the calibration-fit portion of selection-validation. Training and serving share the same serialized fitted sklearn pipeline inside one MLflow model artifact. An explicit deployment action freezes the approved `champion` into append-only deployment history and atomically replaces current deployment state. The API loads only `models:/<name>/<version>` from that state at process startup, unwraps the repository pyfunc wrapper, and returns probability, binary decision, and the selection-validation threshold. Transport validation is separate from semantic validation: model feature roles come from the loaded artifact's `feature_schema` metadata, and every accepted record flows unchanged through the canonical fitted pipeline.

## Candidate/champion lifecycle

Training registers a version and assigns only `candidate`. It logs a frozen promotion-evaluation artifact and manifest containing exact-row SHA-256 identity, dataset/version identity, row count, target distribution, time boundaries, source-data fingerprint, split semantics, a per-artifact byte checksum, and a marker excluding final-test data. `src/registry/promote.py` freezes candidate/champion versions, compares semantic evaluation identity without treating independently written Parquet checksums as dataset identity, independently verifies each artifact against its own checksum, confirms the loaded rows are identical, loads each immutable version's own fitted pipeline, and rescores both on the same rows with each model's stored threshold. Absolute PR-AUC/recall and champion-regression gates use only these rescored metrics. Missing or mismatched evidence, invalid predictions/metrics, model or artifact failures, registry failures, and a moved candidate alias block promotion without changing the existing champion. A complete decision trace, including successful champion-alias movement, is logged to the candidate run. Promotion does not deploy or hot-reload the API. Deployment revalidates the immutable version/run ID and rechecks `champion` immediately before persistence; rollback restores only recorded deployment history and never moves the registry alias. API recreation is explicit after either transition.

## Evaluation strategy

The default outer split remains temporal: earliest 70% train, next 15% development pool, latest 15% final test. The development pool is split in temporal order into 7.5% model-quality selection pool and 7.5% promotion evaluation. The selection pool is split again: its earlier half is used for LightGBM early stopping and optional calibration fitting, while its later half is used for variant metrics and threshold selection. Preprocessing fits on train; promotion uses only its frozen evaluation partition. Routine training neither scores nor exposes final-test metrics; `python -m src.modeling.final_test --model-uri ...` is the explicit release-reporting path and cannot change registry state. Experiment metadata includes variant/configuration details plus exact calibration-fit, selection, source-data, and promotion identities. Synthetic final-test reports are labeled plumbing evidence.

## Known limitations

- The ordinal/drop and uncalibrated baseline remains the preregistered Phase 6 configuration. Frequency encoding and sigmoid/isotonic calibration are implemented variants but were not selected using synthetic results or the canonical final test.
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
- API and MLflow bases, OS package versions and Python dependency constraints
  are pinned. Other Compose service references retain their existing tags.
- Synthetic runs validate plumbing only. Real canonical IEEE-CIS v1 reporting evidence is now recorded separately in `releases/ieee-cis-v1/`.
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

## First remote remediation checkpoint — 2026-10-02

PR #6 merged `fc7f09f7ec0c98af48a097cb1ba65f737b7f56f9` as
`d834583dafe83fd69ea2461efc13366d3a5913ef` after normal CI run `36993627670`
passed all three jobs (lint/type/tests/coverage, Docker, isolated synthetic E2E).
Manual master security run `36994909880` failed on one Gitleaks false positive;
artifact `11221143031` was downloaded and inspected. It identifies the original
API Trivy report's verified SHA-256 on baseline line 492, not a credential.
Build/runtime/Trivy/SBOM steps were skipped, and artifact upload passed.

A focused `.gitleaks.toml` extension retains every default rule and excludes
only that exact digest AND the exact baseline path under `generic-api-key`.
It excludes no commits, entire paths, or other values. Checksum-verified local
Gitleaks 8.30.1 full reachable history: PASS, zero findings. Temporary Git
positive controls: original digest/path excluded; changed digest detected;
same digest at another path detected, all expected exit codes observed.
An initial directory-mode control returned a finding because it uses absolute
paths; controls were rerun in temporary Git repositories matching the workflow.
`git diff --check`: PASS. Only scanner configuration and this evidence note
changed; prior code verification remains applicable. Phase 6 remains blocked.


## Bookworm image evidence and base refresh — 2026-10-02

Supplemental branch run `36995248203`, artifact `11221417110`, completed real
fresh builds, Gitleaks PASS, final-image runtime compatibility PASS and both
SPDX SBOMs PASS, but Trivy FAIL. Each image contains 85 findings: 72 HIGH,
13 CRITICAL, 63 OS and 22 Python. All 22 MLflow/PyArrow entries exactly match,
zero stale entries and zero changed fixed-version snapshots. The 63 remaining
OS findings have no reported Bookworm fix; no OS findings were baselined.
Setuptools/wheel findings disappeared. Reduction is 47/132 (35.6061%) per image,
which does not meet the final gate.

A minimal security prerequisite refreshes both Dockerfiles (API builder and
runtime together) to official Python 3.11.17 slim Trixie, pinned to manifest
index `sha256:45037981b62b34b44602584fccbc4d884d5f7dc92c7ee86bb38a698a79fe1e51`.
`docker buildx imagetools inspect python:3.11.17-slim-trixie` succeeded; amd64
manifest `sha256:922f47525757de33aff59f24cdfc85f412ac4a06aa8af7c7e9028d584b7bcdeb`
reports official source revision `cede844ace77284e32c03b61ebc35cdfc945e862`,
created 2026-10-01. Python minor stays 3.11; MLflow stays 2.22.5; application
pins, API libgomp1, network, MinIO and all model lifecycle semantics remain intact.
Fresh Trixie builds and scans are pending; no OS reduction is inferred from tags.

Base-refresh local verification: focused security tests 62 passed (exit 0), both
Compose profile renders and `git diff --check` passed (exit 0). No Python source
changed; prior full local suite remains 255 passed / 87.50%. Local Docker builds
remain NOT RUN because Colima is stopped; required fresh-image verification is
performed by remote evidence and normal CI before merge.


Trixie branch evidence run `36995801604` failed at API build `pip check` before
runtime/scans: the new base includes `packaging 26.3`; the prefix-installed
MLflow-compatible `packaging 24.2` overlay left the base's old dist-info behind.
The builder log confirms its dependency resolver selected 24.2; MLflow-skinny
2.22.5 requires packaging<25. The smallest fix removes base packaging before
copying the complete builder prefix, restoring one compatible package/metadata
copy without changing requirements or bypassing pip check. MLflow's single-stage
pip install already replaces base dependencies normally. Fresh builds remain
required; no findings are inferred from the failed run.

Prefix-cleanup verification: focused security tests 62 passed, full unit rerun
234 passed, Ruff, Black and Mypy passed, both Compose profiles and diff check
passed (all final exit 0). The existing policy test initially failed by matching
the new packaging uninstall instead of setuptools/wheel; it now explicitly
checks build-tool removal after dependency copy/install and additionally checks
base packaging removal before overlay. No build-tool ordering check was removed.
Black initially requested formatting of that new assertion; formatted and rerun
successfully. Remote image verification remains required.

## Verified external OS blocker — 2026-10-02

Fresh Trixie branch run `36996203975` at
`03192f49cd4c08b0265ac003c9c6846ddb37fb72`, artifact `11221757189`, was downloaded
and every job/step inspected. Gitleaks PASS (zero findings), both fresh image
builds PASS, final-image serialization/MLflow-load/prediction/server CLI checks
PASS, both SPDX 2.3 SBOMs PASS, artifact upload PASS. Trivy/evidence policy FAIL
on real unaccepted OS findings, with no scanner or artifact errors.

Per image: **66 findings, 58 HIGH, 8 CRITICAL, 44 OS, 22 Python, 22 reviewed
matches, 44 unaccepted, zero stale, zero changed fix snapshots**. No setuptools
or wheel findings remain. The reduction from the original 132 is exactly
**66 findings / 50%** per image. All OS findings are HIGH with no reported
Trixie fix. API immutable ID is
`sha256:2a5d6b08e19460d01f8b59a748884832b4f558d91aaf5e62c1c1a80cc5491088`;
MLflow ID is
`sha256:12dac68fc14811cfb4bf75c4a0c3abf7c2599043fbd9b5e640de6302e862e668`.
SPDX package counts are 187 API and 176 MLflow.

The eight OS IDs are CVE-2025-69720 (4 package findings), CVE-2026-16742 (2),
CVE-2026-54369 (1), CVE-2026-9538 (1), and CVE-2026-76642 / CVE-2026-78408 /
CVE-2026-78409 / CVE-2026-78410 (9 each). They cover ncurses, systemd libraries,
acl, perl-base and util-linux packages. No OS entries were added to the baseline.

Primary Debian tracker checks confirm stable Trixie remains vulnerable and
fixes are in testing/unstable for [ncurses](https://security-tracker.debian.org/tracker/CVE-2025-69720),
[systemd](https://security-tracker.debian.org/tracker/CVE-2026-16742),
[acl](https://security-tracker.debian.org/tracker/CVE-2026-54369),
[perl](https://security-tracker.debian.org/tracker/CVE-2026-9538), and
[util-linux](https://security-tracker.debian.org/tracker/CVE-2026-78410).
The official trixie-backports amd64 Packages index was inspected: none of the
17 affected binary packages has a backport. Stable apt refresh/upgrade and a
current official stable Python base therefore do not satisfy the requested gate.
Mixing testing/unstable core libraries, forcibly removing Essential packages, or
redesigning the runtime image would require broader compatibility work beyond
this scoped remediation. No such change or OS risk acceptance was performed.

Final local coverage rerun after metadata cleanup: exit 0, **255 passed**, **87.50%**.
PR #7 remains open with the verified follow-up work; its latest code CI run is
`36996209394` (still running at this checkpoint). PR #6 remains merged as
`d834583dafe83fd69ea2461efc13366d3a5913ef`; master security run `36994909880` failed
before images on the digest false positive, whose validated fix is in PR #7.
Phase 6 remains blocked and has not started. The single next scope decision is
to authorize a separate runtime-base redesign that removes these OS packages,
while retaining Python 3.11 and the existing model/serving contract.

## Scoped runtime redesign in progress — 2026-10-02

User authorized a runtime redesign after comparing safer alternatives. PR #7
head 0f57c602b0bccd97f47e01d17e5ba81fb4f1c814 passed all three normal CI jobs in
run 36996881845; it remains unmerged pending runtime selection. Requested artifact
11221757189 was independently downloaded again: identical 44 OS tuples across
17 packages per image. Exact inventory and strategy comparison are in
`docs/security/`. Dedicated branch `hardening/phase-5-minimal-runtime` evaluates
an ELF-closure rootfs from the pinned official Trixie builder, preserving the
complete CPython installation and package provenance. Runtime and fresh security
validation are pending remote execution; no successful redesign or new count is
claimed. No OS risk is accepted. Phase 6 remains blocked; stash is untouched.

Initial redesign run 36998564053 failed at the ELF closure on the official slim
base's already unavailable Tkinter extension (missing Tcl/Tk), before runtime
or scan steps; Gitleaks and upload passed. The explicit headless exception
rejects unexpected missing libraries and records the excluded broken extension.
No supported stdlib extension is deleted for scanner reduction. Native-model
pins in the MLflow image are aligned with the existing API serialization stack,
and artifact-derived transitive constraints were added for reproducible Python
resolution. Final local full suite: exit 0, 255 passed, 87.50% coverage. Remote
validation of the corrected candidate remains pending; no Phase 6 work started.

Public Wolfi APK index independently confirms Python 3.11.17-r0 is available
without paid image access. The initial Chainguard-catalog-only comparison was
incomplete. The selected candidate changes to a digest-pinned public Wolfi base
with matching builder/runtime packages and venv, eliminating the experimental
custom rootfs collector. Exact Python/native/certificate/timezone roots and
artifact-derived Python constraints are pinned. Real compatibility/security
validation remains pending. No vulnerable OS identity was accepted and no
baseline semantics changed. Phase 6 remains blocked.

Independent inspection rejected the custom-rootfs comparison run's apparent
security success (36999526356, artifact 11223635441): Trivy detected no OS and
scanned only Python, although Syft inventoried retained Debian libraries. No OS
remediation success is claimed from that run. The evidence runner now requests
all OS packages and reconciles Trivy coverage against Syft exact OS identities;
unknown OS or missing inventory fails closed. All 68 focused security tests pass
(exit 0), including six new coverage regressions. Wolfi real builds and initial
runtime checks passed in run 37000028810; its full lifecycle/scans and final
strengthened-policy rerun remain pending. Baseline risk identities are unchanged.

Wolfi run 37000028810 / artifact 11223930869 passed real final-container imports,
cross-image API-created model loading, serialization/pyfunc prediction, native
stdlib/certificates/timezone, both live MLflow server checks and nine-step
non-root API lifecycle. Trivy recognized Wolfi and scanned 38 APK packages with
zero OS findings. Each image had 26 Python findings: 22 reviewed, four new
unreviewed in pip's private vendors, no stale entries. Latest pip 26.2.1 still
contains those vendors. Final runtime now validates dependencies then uninstalls
pip itself by its supported operation; no OS-owned package files are deleted.
The no-installer runtime must pass all remote checks again. OS coverage matches
full Debian epoch/release identities and APK versions. All 71 focused security
tests pass. No new Python or OS finding is accepted. Phase 6 remains blocked.

Both-image OS inventory coverage was independently validated against the original
Debian artifact (88 API / 87 MLflow exact package identities) and first Wolfi
artifact (38 / 38), all PASS with epoch/release-aware reconciliation. All 38
actual APK package versions are now locked, including the glibc-2.44 2.44-r7
provider selected by the pinned base; builder and final runtime use the same lock.
This prevents transitive OS provider drift. The first API/native model stack is
otherwise unchanged, with both-image Python constraints pinned as documented.


## Fully locked runtime branch verification — 2026-10-02

Selected strategy C is digest-pinned public Wolfi with upstream Python 3.11.17,
all 38 native APK versions pinned and complete application Python constraints.
[Runtime comparison](security/runtime-strategy.md) and the exact 44-row/17-package
[original inventory](security/trixie-os-inventory.csv) document the decision.
PR #7's Trixie runtime is superseded; its Gitleaks fix and follow-up evidence are
retained in PR #8. No change to application inference, model methodology,
registry/deployment lifecycle, authentication/network architecture, MLflow major
version, MinIO, or vulnerability risk baseline is included.

Authoritative branch run **37001197837**, source
`a3e4fef55681277dd23e728b983c9224e5aa3a25`, artifact **11223908196**, completed
successfully. It was independently downloaded and every report inspected:

- Gitleaks 8.30.1: full history, zero findings, PASS.
- Fresh API and MLflow builds: PASS; pip check in builder and final layer before
  pip removal, then both final runtimes prove no pip/setuptools/wheel distribution.
- Both-image native imports, trust store, Europe/Istanbul timezone, curses
  terminfo, UUID paths, joblib/MLflow pyfunc roundtrip and an API-created model
  loaded in both images: PASS. MLflow 2.22.5 live server health: PASS in both.
- Non-root API container's nine-step synthetic training/candidate/promotion/
  deployment/startup/health/immutable readiness/single and batch prediction:
  all PASS. These are plumbing checks, not IEEE-CIS performance.
- Checksum-pinned Trivy 0.69.3: ACCEPTED in both; **22 findings/image**, **14 HIGH**,
  **8 CRITICAL**, **0 OS**, **22 Python**, **22 reviewed**, **0 unreviewed**,
  **0 stale**, **0 changed fixed-version snapshots/newly fixable findings**.
- OS coverage: recognized Wolfi; **38 Trivy / 38 Syft** exact installed OS
  package identities match in each image. Syft 1.52.0 SPDX 2.3: PASS;
  129 API packages and 119 MLflow packages.
- API immutable ID:
  `sha256:e1970703e50b299f5c57f6b869bba033579d548b6a251672023003286abaeb89`.
- MLflow immutable ID:
  `sha256:eb25d3df7a281828653bf88cff206def5aa5111b718c83e0cd6a1a10888d3bce`.
- Trivy-reported sizes: 993,807,872 API / 970,569,216 MLflow bytes.

Per-image HIGH/CRITICAL progression: **132 → 85 → 66 → 22**.
OS progression: **107 → 63 → 44 → 0**. The final 22 are explicitly reviewed
MLflow/PyArrow residuals expiring 2026-11-01, not remediated vulnerabilities.

Final local verification (all final commands exit 0): Ruff and Black across
src/tests/scripts/docker; Mypy src; 71 focused security tests; 243 unit tests;
21 integration tests; complete 264-test coverage suite at **87.50%** (75% floor);
virtualenv pip check; both Compose profile renders; workflow YAML parse; exact
reviewed Python audit (28 unique advisories across 2 packages); diff check.
The first make audit attempt exited 2 because system python was absent; rerunning
with the repository venv on PATH passed. CSV CRLF initially caused diff-check
exit 2; LF normalization passed. Initial Ruff import/length errors were fixed.
All earlier container failures and the invalid custom-rootfs security green are
explicitly documented above and in the decision record.

Local docker info exited 1: Colima is stopped. Local container builds/Trivy/Syft
are NOT RUN; GitHub Actions provides authoritative evidence. Complete
production-like MinIO/NGINX E2E is NOT RUN and remains upstream-blocked; no
production-stack success is claimed. Runtime tests cover Linux amd64; arm64
execution is not claimed. Stash remains
`3384b5a200c73bd07a36dabb11f274280d72ca20`.

Normal PR CI and post-merge master security evidence remain pending at this
checkpoint. Phase 6 remains blocked until those last gates pass and is not started.


## Final master security-remediation verification — 2026-10-02

PR #8 final head `2e7eab7db8cea36f72de21f07de257fe3ea4fae3` passed all three
normal CI jobs in **37002063471** and merged as
**`31449e27bace2a4091fa97e6f5cd04fe29c0d9b6`**. PR #7 was closed, unmerged,
as superseded: its Trixie runtime is replaced by Wolfi, while its exact Gitleaks
fix and evidence follow-up are included in PR #8. PR #6 remains merged as
`d834583dafe83fd69ea2461efc13366d3a5913ef`.

Fresh **master CI 37003091466** passed all three jobs: lint/type/tests/coverage,
Docker, and isolated synthetic lifecycle E2E. Fresh **master security run
37003134524**, source **31449e27bace2a4091fa97e6f5cd04fe29c0d9b6**, artifact
**11225255513**, passed every build, runtime, tool, scan, SBOM and upload step.
The artifact was independently downloaded and evaluated again with the current
exact baseline and OS-coverage validator; report identities/summary decisions
match, and Gitleaks's full-history report is empty.

Each image: **22 total HIGH/CRITICAL findings, 14 HIGH, 8 CRITICAL, 0 OS,
22 Python, 22 exact reviewed matches, 0 unaccepted, 0 stale reviews,
0 changed fixed-version snapshots / newly fixable findings**. No OS identity or
new Python identity was added to the unchanged reviewed baseline. This is
ACCEPTED residual risk, not vulnerability-free performance or a clean Trivy
finding count. Checksum-pinned tool versions remain Gitleaks 8.30.1, Trivy 0.69.3
and Syft 1.52.0. Both SPDX 2.3 documents pass; all **38/38** OS identities match
in each image. SPDX package counts are 129 API / 119 MLflow.

Final master immutable image IDs:

- API: `sha256:9ba7e05ab04e8f85ac0bfeaa29a2cac0bf0852ec0d1b7c697801ad72b5839d71`.
- MLflow: `sha256:7c3329eb8c4d0399c4ef28840d332ea4e5dcff511c80c5994ecc6fcbd06c35ed`.

Trivy-reported sizes are 993,807,872 API / 970,569,216 MLflow bytes. Original
sizes were 1,048,168,448 / 998,974,464 bytes. Per-image vulnerability progression
is **132 → 85 → 66 → 22**, with OS **107 → 63 → 44 → 0**. The redesigned runtime
eliminates the 44 unaccepted OS findings without accepting them. The retained
22 MLflow/PyArrow risks expire 2026-11-01 and require continued network containment.

All requested native/model/API/MLflow runtime checks pass on Linux amd64,
including cross-image loading and real API health/readiness/prediction without
runtime pip/setuptools/wheel. Both build stages and final layers performed pip
check before installer removal. Local final verification is 264 tests at 87.50%
coverage, 243 unit / 21 integration, 71 focused security cases, passing lint,
format, type, dependency audit, Compose and diff checks as recorded above.
Local Docker checks were NOT RUN because Colima is stopped; remote checks are
authoritative. The complete MinIO/Postgres/S3/NGINX production-like lifecycle
remains NOT RUN/upstream-blocked, independently of the completed security gate.
Synthetic results are plumbing evidence only. No arm64 execution is claimed.

This completion record changes documentation only; production/runtime/evidence
source remains identical to verified master 31449e27bace2a4091fa97e6f5cd04fe29c0d9b6.
No Phase 6 work started. Original stash remains
`3384b5a200c73bd07a36dabb11f274280d72ca20` and was never applied or mutated.

**PHASE 5 SECURITY REMEDIATION VERIFIED.**
**PHASE 6 MAY START.**


## Phase 6 canonical model evidence — 2026-10-02

Authorized branch `release/phase-6-canonical-ieee` began at verified master
`825967251d19b97d6650fae4f3c889f032ea98f7`. The original stash remains
`3384b5a200c73bd07a36dabb11f274280d72ca20` and was never applied or mutated.
All four real Kaggle files were already present in ignored storage. No dataset
substitute, model/configuration search, object-store replacement or runtime/risk
baseline change was performed.

**REAL IEEE-CIS EVIDENCE:** release `ieee-cis-v1`, full 590,540 labelled source
transactions; train identity 144,233 rows. Unlabelled competition test transaction
506,691 / identity 141,907 rows were audited, not used for model scoring. Each
file has unique TransactionID, correct target placement, nonnegative/nonmissing
transaction times, orphan-free identity joins, ordered schema and missingness
metadata. Exact hashes live in the safe dataset manifest. Dataset fingerprint:
`a01f77eaa792c8346a876102fa3c717f7361ccd4e14fe1a57735b86816c7ab75`.

Canonical configuration SHA-256:
`d9983ad820d40bdbf72590690930b02967eb8ad2add7e4b4c7d7bdd1c6beda15`.
Preregistration commit `81cfdea4ccf98305e7e1aa5a6105c8cddd10996d` precedes training
and final reporting. Preserved ordinal/drop, no calibration, default LightGBM
parameters, seed 42, selection-only best_f1 threshold, FN=25/FP=1 reporting costs.
Stable temporal partitions: train 413,378; early-stopping/calibration-fit 22,145;
selection 22,145; promotion 44,291; final reporting 88,581. Exact nonoverlapping
time boundaries/full-row fingerprints are in `split_manifest.json`.

Training run `a7d516e70f714caeb67f35bd2c30762f` from clean preregistration source;
registered/candidate version `1`, first legitimate champion version `1` in the
isolated SQLite/local-artifact registry. Promotion passed loadability, signature,
smoke probability, frozen artifact integrity/row identity, absolute gates and
candidate alias stability. Promotion PR-AUC 0.5132486707022708 and recall
0.48171152518978605 are gate evidence, not final metrics. No existing champion
was present, so the regression comparison was explicitly not applicable; no
synthetic-versus-real comparison was fabricated.

Explicit deployment `20261002T125321465784Z-4cf8f6d7fe324477b6f25ae0bfa4e886`
records version `1`, the same run, and source
`cf83cf008d2332f263611dcb752d63f6c96d48a8`. Append-only history equals current
state and its checksum is recorded. Deployment git_dirty=true reflects creation
of the safe untracked output manifest before the command; executable source was
committed. Direct local-lite API served `models:/ieee_fraud_lgbm/1`; health,
readiness run/version, single/batch predictions, exact fitted-pipeline parity,
stored threshold and malformed-request 422 checks passed. The first evidence
check expected a URI in model_source; the existing field returns `version:1`.
The validator was corrected without changing the service contract. Requests used
handmade unrestricted values; no raw IEEE fixture was saved.

Pre-final review/deployment freeze commit
`c54bf3380df626026d6921be93f9667bbb2f1374` precedes one successful invocation of
`src.modeling.final_test` on immutable `models:/ieee_fraud_lgbm/1`. No failed
final invocation, configuration revision or second final scoring occurred. Final
row fingerprint:
`da1f461c4513777e09e8818f3402df1e4a4833e3986e68db26a68ef6deb4ffb4`.

| Real final-report metric | Exact value |
|---|---:|
| Rows | 88581 |
| PR-AUC | 0.5229777963628136 |
| ROC-AUC | 0.8956263566933376 |
| Precision | 0.4429896344789962 |
| Recall | 0.5267596496918586 |
| F1 | 0.4812564824418432 |
| Log loss | 0.09384780195084294 |
| Brier score | 0.02256234669894403 |
| Threshold | 0.1508937436017237 |
| Fraud rate | 0.03480430340592226 |
| Expected illustrative cost | 38517.0 |
| Cost per sample | 0.434822365970129 |

Confusion counts TN=83456, FP=2042, FN=1459, TP=1624. The final holdout never
fit preprocessing, model, early stopping, calibration, threshold or promotion.
The source-to-served-model-to-final-report chain passes the safe release verifier,
including optimized Python mode. Raw source files, Parquet, input examples, model
binaries, SQLite and artifact stores are ignored and unpublished.

Model card, architecture, recruiter-first README and reproduction guide now
reflect this evidence and separate **SYNTHETIC SMOKE / PLUMBING EVIDENCE**.
The local training runtime is macOS arm64 Python 3.11.15; final Linux amd64
container compatibility remains independently tested with synthetic artifacts.
The canonical private binary was not tested inside a container and is not
redistributed. Live performance, fairness, cloud SLA, delayed-label monitoring
and a complete MinIO-backed production stack are not claimed.

Final local verification: 10 release-focused regressions PASS, unit 253 PASS,
integration 21 PASS, full suite 274 PASS, coverage suite 274 PASS at 87.77%
(75% floor).
Ruff, Black check, Mypy, pip check, both Compose renders, safe release verification
and diff check PASS. Initial sandbox dependency audit failed closed (no valid
JSON); authorized network retry PASS matching 28 advisories across two packages.
Local production validator exit 1 because Colima is stopped; no services/volumes
started, diagnostic/teardown commands also could not connect. Remote explicit
production workflow `37009722147` completed FAIL: aligned image builds PASS,
lint/test/coverage job PASS and synthetic E2E job PASS, but mandatory production
startup could not pull `minio/minio` (repository unavailable/access denied).
Project `phase4e2e-c42d2eb9` had no created services; diagnostics and isolated
`down -v --remove-orphans` completed. No complete production-like PASS is claimed.

Security workflow `37010476773`, source
`c54bf3380df626026d6921be93f9667bbb2f1374`, artifact `11227139997` SUCCESS. The
actual artifact was downloaded and independently re-evaluated with the unchanged
exact policy: each image 22 findings, 14 HIGH / 8 CRITICAL, 22 reviewed matches,
zero issues/unaccepted/stale/newly-fixable changes; Wolfi 38/38 Trivy/Syft OS
coverage PASS, SPDX SBOMs PASS, zero Gitleaks findings, runtime imports,
cross-image serialization/live MLflow and synthetic immutable API lifecycle PASS.
The canonical private binary was not container-tested.

Final implementation source `0cbc92419b55e69d0ee64a197c562a62b64242bb` passed
all three normal PR CI jobs in `37011458910`: 253 unit / 21 integration /
274 coverage tests at 87.77%, aligned API image build, and 9/9 synthetic
lifecycle/API steps. Its fresh security run `37011457845`, artifact
`11227802287`, also SUCCESS; downloaded reports were independently re-evaluated
with exactly the same 22 reviewed findings / 14 HIGH / 8 CRITICAL, zero
unaccepted/stale/changed-fix findings, 38/38 OS coverage, SPDX PASS, zero
Gitleaks findings and complete runtime/server/synthetic API PASS. No risk
baseline or image/source implementation changed in this final evidence follow-up.

A final local inspection reconciled candidate/champion/deployed version 1 and
the run, plus all 48 private model artifact hashes. Its first ad hoc check
compared MLflow integer version 1 with string "1"; normalization corrected the
check without changing aliases or model state. The 1,827,518-byte fitted
pipeline SHA-256 is
`4900d5c0746e72ded345ddf5f58fc8aef59af4b4b81f905cbd125c56c46008c5`.
Only safe hashes are published. Temporary API was stopped after verification;
the private isolated model/store/deployment remain available.

PR #9 is open for review with the real evidence and exact blocker. No unresolved
review threads existed at inspection. All modeled/serving results remain valid;
no final-test rerun occurred. Master remains
`825967251d19b97d6650fae4f3c889f032ea98f7`. Merge, release tag and post-merge
master workflows are NOT RUN because the mandatory production-like gate fails.
The final follow-up changes only safe evidence/documentation, not Python source,
models, configuration, runtime or the risk baseline.

Phase 6 is **BLOCKED on closure**: its specification requires production-like E2E
and all required checks to pass. An infrastructure limitation must not silently
become a completion waiver. No verified release tag or merge is claimed yet.

## Phase 6 authorized closure exception — 2026-10-02

The user explicitly authorized Phase 6 completion with this narrow external
infrastructure exception. **Production-like E2E = NOT VERIFIED.** Reason:
**unavailable legacy Community MinIO container distribution**; existing
`minio/minio` repository/image is unavailable/access denied. Failed workflow
`37009722147` remains failed evidence, not PASS. This is an external infrastructure
limitation independent of the canonical model release. Local-lite real IEEE-CIS
lifecycle is verified; canonical release evidence remains valid. No production
readiness is claimed for the blocked full stack. MinIO, the production validator,
canonical metrics, model, promotion, deployment, evaluation and security policy
remain unchanged. No final-test selection or scoring was rerun.

PR #9 head before this prose update was `47baa0374b3c531b1059cddd2e94565cab1a13c5`.
Normal CI `37012769052` is SUCCESS (all three jobs). Security `37011457845` /
artifact `11227802287` remains SUCCESS/unexpired; its actual artifact was downloaded
and re-evaluated: 22 accepted residuals per image, zero policy issues, Wolfi 38/38
OS coverage, SPDX 2.3 PASS, Gitleaks zero findings. No unresolved review threads.
Safe release verification passed in normal and optimized Python modes (exit 0).
Tracked-file inspection found only synthetic CSV fixtures and the reviewed public
OS inventory; no restricted rows, model binaries, MLflow databases, credentials,
private generated artifacts or absolute personal paths are tracked.
Documentation-only verification uses diff checks and the safe offline release
verifier; heavyweight runtime/model tests are not applicable to this prose change.
Original stash remains `3384b5a200c73bd07a36dabb11f274280d72ca20`.

## Phase 6 final verified master / release checkpoint — 2026-10-02

PR [#9](https://github.com/bulsahkecici/end-to-end-mlops-fraud-detection/pull/9)
merged with a normal two-parent merge commit. Approved final PR head:
`d8a00f1b58cf801e4902f1f2cef98ddccfdd6bb0`; final PR CI `37015662481` SUCCESS,
no unresolved review threads. **Verified master / merge / tagged release SHA:**
`243212ce211ceedb02e7230a53f4017b33d3decb`. Local master was fast-forwarded to
origin/master, and the merged tree exactly matches the approved PR tree.

[Final master CI 37016887460](https://github.com/bulsahkecici/end-to-end-mlops-fraud-detection/actions/runs/37016887460)
SUCCESS on that exact SHA; push CI `37016819352` also SUCCESS. Actual logs:
253 unit tests, 21 integration tests, 274 full coverage tests, **87.77%** coverage
(75% minimum), dependency integrity/reviewed audit, Ruff/Black/mypy, both Compose
profiles, API image build and **9/9 synthetic lifecycle/API steps** PASS.
Production-like opt-in was not run by normal CI and is not a passing gate.

[Final master Security Evidence 37016822537](https://github.com/bulsahkecici/end-to-end-mlops-fraud-detection/actions/runs/37016822537)
SUCCESS on that exact SHA; actual artifact **11230064681** was downloaded outside
the repository and independently re-evaluated using the unchanged baseline and
OS coverage validator. Artifact digest:
`sha256:52540cfda0263025ab502b11ad156872541cb4908e52871ceadc49d74dd5c5ad`.
Each image: 22 accepted Python residuals (14 HIGH / 8 CRITICAL), zero OS findings,
zero unaccepted/stale/changed-fix findings, Wolfi **38/38** exact Trivy/Syft OS
identities, SPDX 2.3 PASS. Full-history Gitleaks: zero findings. All fresh image
builds, native imports, cross-image serialization, live MLflow and the nine-step
non-root synthetic API lifecycle PASS. Accepted residual risks still expire
2026-11-01; they are not remediated vulnerabilities. Canonical private binary
container execution remains unclaimed.

Annotated **`ieee-cis-v1`** tag targets verified master commit
`243212ce211ceedb02e7230a53f4017b33d3decb` (tag object
`14af11c86192ed732890b4d8ecdbedbd678a08dc`). The public
[GitHub Release](https://github.com/bulsahkecici/end-to-end-mlops-fraud-detection/releases/tag/ieee-cis-v1)
contains only canonical real metrics, dataset fingerprint, run/version/source
identity, reproduction links and the explicit MinIO limitation; **zero attached
assets**. The original manifests, model, promotion, deployment, metrics and
security/evaluation evidence were not changed. Final-test invocation count
remains one; no final-test selection leakage or rerun occurred.

**Production-like E2E = NOT VERIFIED.** Reason: **unavailable legacy Community
MinIO container distribution**; existing `minio/minio` repository/image is
unavailable/access denied. Failed workflow **37009722147** is preserved as failed
evidence. This is an **external infrastructure limitation**. The explicitly
authorized exception permits Phase 6 closure; it does not establish a full-stack
PASS or production readiness. **Local-lite real IEEE-CIS lifecycle is verified;
canonical release evidence remains valid.** MinIO and the production-like validator
remain unchanged; no AIStor/other object store or infrastructure redesign occurred.

The final closure record is documentation only, following the tagged verified
master checkpoint; it does not move the immutable release tag or change runtime
source. Its local checks passed (exit 0): safe release verifier in normal and
optimized Python, 10 release regressions, full-history Gitleaks (zero findings),
tracked-file/private-artifact inspection and `git diff --check`. Runtime/container
and model scoring checks were not rerun for this prose-only record because they
are not applicable; the exact master executions above are authoritative.
Tracked storage contains only placeholders, synthetic test fixtures and safe
release metadata/public security inventory. No raw IEEE-CIS data, restricted rows,
model binaries, MLflow DBs, credentials, absolute personal paths or generated
private artifacts are tracked. The original stash remains untouched:
`3384b5a200c73bd07a36dabb11f274280d72ca20`.

**PHASE 6 CANONICAL IEEE-CIS RELEASE VERIFIED.**

## Final audit local verification — 2026-10-02

Dedicated `release/final-audit-portfolio` starts from verified master
8e4c4b74d5712e00691c795be561ac70f29d4295. README, model card, architecture,
reproduction and MIT/source-versus-data licensing are polished. Safe release
manifests and ieee-cis-v1 tag are unchanged. Local checks: unit 253, integration
21, full 274 and coverage 274 at 87.77%; Ruff/Black/mypy, pip check, reviewed
dependency audit, both Compose renders, normal/optimized safe release verifier,
relative links/private-artifact inspection, full-history Gitleaks zero findings
and diff check all PASS (exit 0). See final-audit.md for scope and exceptions.
Master protection and repository metadata are configured; three extant fully
merged branches removed, two with unique history retained. No image/security
baseline/model/evaluation changes, no retraining and no final-test rerun.
Original stash is unchanged: 3384b5a200c73bd07a36dabb11f274280d72ca20.
PR/master remote verification remains pending at this pre-commit checkpoint.
