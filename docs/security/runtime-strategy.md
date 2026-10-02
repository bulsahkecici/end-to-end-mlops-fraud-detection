# Phase 5 production runtime decision

The selected runtime is **public Wolfi with upstream Python 3.11.17**, using
matched builder/runtime packages and a venv. Both images preserve MLflow 2.22.5,
the API/model contract, auth/network configuration and separate promotion and
deployment lifecycle. No model methodology or Phase 6 work is included.

## Exact original OS inventory

Artifact 11221757189 from run 36996203975 was independently downloaded. API and
MLflow have identical tuples of CVE/package/severity/installed/fixed version:
**44 HIGH findings across 17 binary packages and eight CVEs**. All have no
reported stable Trixie fix. [The CSV](trixie-os-inventory.csv) lists every tuple,
SPDX DEPENDENCY_OF edges, origin, direct application need, and actual ELF/data
closure evidence. SPDX package dependencies alone are not reachability proof.

Working CPython's curses, readline and UUID extensions select libncursesw6,
libtinfo6 and libuuid1; terminal data additionally selects ncurses-base. Their
need is preservation of supported stdlib behavior, not a direct application
requirement. The other 13 packages belong to base shell/coreutils, login/mount,
Perl or apt infrastructure and are absent from a successfully executed native
closure. No finding was dismissed using its package name alone. Wolfi replaces
that Debian package set with its supported native dependency closure.

## Five-strategy comparison

| Dimension | A: official Python slim Trixie | B: Debian distroless | C: public Wolfi, selected | D: custom minimal rootfs | E: selective apt removal |
| --- | --- | --- | --- | --- | --- |
| Python 3.11 | Exact 3.11.17, proven | Native Debian13 Python is 3.13; Debian12 uses 3.11 but reverts distro/ABI source | Exact upstream package 3.11.17-r0, proven | Copied current 3.11.17, proven | Same as A if safe removal |
| MLflow 2.22.5 | Proven wrapper/CLI | Unverified on a matched 3.11 overlay | Proven import, CLI and live server | Proven imports/live server | Unverified after removal |
| LightGBM | API libgomp import/model tested | Needs matching OpenMP/C++ libraries | Both images import, fit, serialize and load; pinned libgomp/libstdc++ | Complete discovered native package closure, tested | Removal must preserve libgomp/C++ |
| pandas/numpy/scipy/sklearn | API model stack tested | Wheels require matched glibc and Python | Both-image imports and API-created cross-image model load proven | Both-image imports/load proven | Same wheels; dependency deletion risk |
| PyArrow | 17.0.0 API / 19.0.1 MLflow, proven | Requires native library/ABI tests | Existing versions retained, imported and used in lifecycle | Existing versions tested | Existing versions, unverified after removal |
| glibc | Debian 2.41 | Debian13 maintained glibc; copied runtime needs reconciliation | Actual glibc-2.44 2.44-r7 provider; existing binary wheels tested | Same Debian library source as builder | Same Debian libraries if retained |
| SSL/certificates | Existing support | Base includes glibc/SSL/certs | Trust-store/native SSL checks pass; exact cert package pinned | Explicit default paths/provider preserved and tested | Apt must retain SSL/trust files |
| Timezone/locale | Existing full defaults | Base includes tzdata | Timezone pin, Europe/Istanbul lookup, C.UTF-8, proven | Explicit timezone/C.UTF-8/native data roots tested | Retention must be demonstrated |
| Subprocess/shell | Shell present | No shell by default; argv CLI possible | Upstream BusyBox shell retained; actual MLflow argv subprocess passes | No shell; actual MLflow argv subprocess passes | Removing Essential shell/tool chains is unsafe |
| Serialization/model loading | Existing artifact tested | No actual compatible 3.11 application image | Joblib and API-created MLflow pyfunc loaded in both images | Same tests pass | Not established after removal |
| API/health/readiness/prediction | Prior normal CI and smoke | Not proven | Full non-root nine-step container lifecycle passes | Same lifecycle passed | Not proven after removal |
| Debugging/operability | Familiar Debian/apt/shell | Python/logs plus external debug container | Standard APK inventory, shell and Python/logs; rebuild immutable image for fixes | Python/logs only; repository maintains assembler and loader/data roots | Familiar tools but forced Essential removal breaks package assumptions |
| Image size, Trivy reported bytes API / MLflow | 1,048,168,448 / 998,974,464 | No compatible application image built; unknown | Initial no-pip evidence 993,805,312 / 970,566,656; locked final artifact recorded below | 978,921,472 / 955,992,064 | Not built; unknown |
| HIGH/CRITICAL count | 66 = 44 OS + 22 Python | Not measured; no zero-CVE claim | 22 = 0 OS + 22 already reviewed Python; all 38 OS packages covered | Apparent 22 **invalid**: Trivy missed the OS while Syft retained vulnerable Debian libraries | Safe removal cannot eliminate the Essential dependency closure; exact new count unknown |
| Reproducibility | Base pinned; apt/transitive resolver mutable | Additional base provider and overlay reconciliation | Base digest, all 38 APK versions, complete application constraints pinned; binary wheels only | Base pinned but apt mutable, custom copied-package provenance | Mutable apt; forced deletion is fragile |
| Maintenance | Simple but stable fixes unavailable | 3.11 runtime overlay requires closure maintenance | Standard upstream packages and short Dockerfiles; deliberate lock refreshes require all gates | Custom ELF/data/loader/NSS logic and scanner-recognition work | Essential-package surgery has high maintenance/regression risk |

A and E cannot safely eliminate the remaining Debian Essential/base tooling
closure. B's native Debian13 interpreter violates the 3.11 requirement; combining
a copied 3.11 interpreter with distroless base adds the custom closure and
multiple-provider reconciliation work. C avoids a bespoke distro build and all
optional-extension/rootfs deletion logic. D was investigated but rejected after
independent artifact inspection found a scanner coverage defect. C's selection
rests on standard package maintenance and actual compatibility, as well as
material OS reduction. Alpine/musl and unstable Debian mixing were not selected.

Primary upstream sources checked 2026-10-02:
[distroless Python matching requirements](https://github.com/GoogleContainerTools/distroless/blob/main/python3/README.md),
[distroless base contents](https://github.com/GoogleContainerTools/distroless/blob/main/base/README.md),
[Chainguard Python catalog](https://images.chainguard.dev/directory/image/python/versions),
[public Wolfi package repository](https://github.com/wolfi-dev/os), and the downloaded
[actual APK index](https://packages.wolfi.dev/os/x86_64/APKINDEX.tar.gz).
The catalog's paid 3.11 ready-made image does not prevent using the public upstream
3.11 package; this was verified independently rather than assumed from marketing.
The inspected distroless comparison index was
`sha256:a0d70d6a97cd697d9362bc2aae4a6560dd65817e365d0043b07325a97975dc91`.

## Production design and compatibility prerequisite

Both Dockerfiles use public Wolfi index
`sha256:824f77df45397eb954dfb963db255907ee8842e3446353ce93d688e5e862f51d`.
The same [38-package OS lock](../../docker/wolfi.packages) applies in builder and
runtime, including Python, actual libc provider, OpenMP/C++ runtime, certificates
and timezone. Exact unavailable versions fail the build. Python application
constraints originate from the original SBOM; API model pins remain unchanged.
MLflow adds LightGBM and aligns numpy/pandas/sklearn/joblib with API as the minimal
prerequisite for the requested both-image production-model loading. PyArrow
remains API 17.0.0 / MLflow 19.0.1 to preserve the exact reviewed identities.

Both builders and final layers run pip check. After that check the final layer
uninstalls pip itself through its supported uninstall operation; setuptools and
wheel distributions were already removed after dependency installation. This
removes pip 26.2.1's newly vulnerable private copies of setuptools, msgpack and
urllib3. Independently inspecting latest upstream pip confirmed those vendor
versions remain there; upgrading application urllib3 cannot fix private copies.
No OS-owned ensurepip/wheel payload or APK metadata is deleted or rewritten.
Final runtime smoke verifies build-tool distributions are absent and all actual
services/model paths work without installers. pip check after removal is not
applicable; the successful build-time checks remain required.

The API retains uid/gid 10001. MLflow retains its existing user and volume
behavior. Health checks use Python argv. No registry alias, deployment state,
auth setting, network boundary, MinIO implementation or application source is
changed. Projects/environment creation is a build/development operation; serving
loads the existing fitted model in-process. Use the builder for pip-based work.

## Evidence coverage and failure history

PR #7 head 0f57c602b0bccd97f47e01d17e5ba81fb4f1c814 passed all three CI jobs
in run 36996881845 before this work. Its Trixie runtime is superseded by C; the
exact Gitleaks exception and other follow-up fixes are retained in PR #8.

Custom-rootfs runs 36998564053 and 36998784035 failed on the official slim base's
already unavailable Tkinter libraries. Run 36999096176 caught an inspection
context error for psycopg2's private auditwheel libraries. Neither failure was
represented as passing. D run 36999526356 / artifact 11223635441 passed runtime,
but its apparent security ACCEPTED is rejected: OS.Family=none, only Python
results, and Syft retained libuuid/ncurses. The final evidence runner requires
recognized Debian/Wolfi OS, lists all OS packages, and reconciles every Syft OS
name/version against Trivy. Missing coverage fails closed. Full Debian epoch and
release fields are preserved. Regression tests capture each blind-spot case.

First Wolfi run 37000028810 / artifact 11223930869 passed real runtime but failed
with four unreviewed Python vendor findings per image. They were removed by
uninstalling final-runtime pip; none was accepted. Strengthened-policy run
37000880713 / artifact 11223812938 then passed with 22 exact reviewed Python
findings, zero OS/unreviewed/stale findings, zero changed fix snapshots, and
38/38 Trivy/Syft OS coverage. Both SPDX documents parse, all tools are checksum
pinned, and both scans bind exact immutable image IDs. Final all-package-lock
run 37001197837 also completed successfully; its artifact is independently
inspected and recorded in PROJECT_STATE.md before merge.

## Limits and refresh policy

Synthetic tests prove container/model plumbing only, not IEEE-CIS performance.
Authoritative containers run on GitHub's Linux amd64 runner; arm64 execution is
not claimed. Existing MinIO distribution prevents the independent complete
production-like Postgres/S3/NGINX stack test, which is not represented as passing.
The 22 existing MLflow/PyArrow risks remain reviewed residuals, not remediation
or false positives; their reviews expire 2026-11-01. No OS residual is accepted.

Pinned packages still depend on upstream artifact availability. This provides
reproducible dependency resolution, not a bitwise image claim: file timestamps,
runner and scanner databases may change. Intentional base/package refreshes must
repeat native/cross-image/live-service tests, normal CI, Gitleaks, SPDX and the
unchanged exact-baseline plus OS-coverage gates. APK/shell remain ordinary distro
components with measured coverage; the design makes no claim that they are absent.
