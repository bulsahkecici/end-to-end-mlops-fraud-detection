# Phase 5 runtime strategy comparison

PR #7 exact head `0f57c602b0bccd97f47e01d17e5ba81fb4f1c814` passed
all three CI jobs in run 36996881845. It remains unmerged pending runtime review.
Artifact 11221757189 was independently downloaded from GitHub. Its API and MLflow
OS tuples (CVE, package, severity, installed/fixed version) are identical:
44 HIGH findings, 17 binary packages, eight CVEs. The adjacent CSV lists every
identity and actual SPDX DEPENDENCY_OF edges. SPDX edges establish Debian package
relationships; they do not establish application reachability. Python packages
are not directly dependent on those 17 binary packages in the SPDX graph.

## Alternatives

| Option | Compatibility / security evidence | Reproducibility / maintenance / operation |
| --- | --- | --- |
| A: official 3.11.17 slim Trixie | Prior real containers pass serialization, pyfunc and CLI checks; 66 findings: 44 OS + 22 reviewed Python. All requested native wheels use glibc; certificates, timezone and full stdlib present. | Pinned digest; apt refresh still time-dependent. Shell and apt ease diagnostics but retain unnecessary system tools. Prior artifact has exact IDs; fresh size is recorded by image inspect. |
| B: Debian distroless Python/runtime | Native Python3 Debian13 is 3.13, violating required 3.11. Debian12 Python3 uses 3.11 but reverts the OS remediation and differs from the current builder ABI. Base-debian13 could host copied CPython plus missing libraries, requiring the same closure work as D and a second base provider. Counts/size for an unbuilt application image are unknown. | Maintained glibc, certs and timezone; no shell. Matching builder/runtime required by upstream docs. Overlay must retain additional package metadata and reconcile library versions. Not selected. |
| C: Chainguard/Wolfi Python | Public latest is 3.14; upstream catalog lists 3.11 behind access contact. glibc avoids musl risk but access and exact wheel/pyfunc compatibility are unverified. No application count/size claim. | Would add another distro, package mapping and credentials/commercial-access dependency. Building Wolfi Python ourselves increases maintenance. Not selected. |
| D: minimal rootfs assembled from current builder | Keep entire CPython 3.11 and installed wheels, discover every ELF dependency, copy complete owning Debian library packages. Preserve certs, timezone, locale support, NSS configuration and SSL provider. No optional Python module is silently removed. Exact counts, size and compatibility pending remote execution. | Single stable Debian source; no bespoke distro compilation or unstable mixing. Preserve dpkg status/file metadata, license files and per-ELF ownership evidence. No package manager/shell in final image; diagnostic Python and logs remain. Rebuild rather than patch live images. |
| E: selectively remove packages from official image | Debian Essential login/util-linux/perl tooling cannot safely be removed with ordinary apt purge. Force removal violates package-manager assumptions and can leave libraries and vulnerabilities. Smaller safe removals cannot eliminate their retained Essential closure. | Small source diff but dangerous deletion/maintenance semantics if forced. Not selected. |

Primary sources checked 2026-10-02:
[distroless Python requirements](https://github.com/GoogleContainerTools/distroless/blob/main/python3/README.md),
[distroless base contents](https://github.com/GoogleContainerTools/distroless/blob/main/base/README.md),
[Chainguard Python versions](https://images.chainguard.dev/directory/image/python/versions).
The inspected distroless base-debian13 nonroot index is
`sha256:a0d70d6a97cd697d9362bc2aae4a6560dd65817e365d0043b07325a97975dc91`;
this is comparison evidence, not a dependency of the selected design.

## Selected design and limits

D is the test candidate: retain Trixie as the matched builder/library source and
replace only the final rootfs. All native dependency packages are copied whole,
not selected libraries with hidden provenance. The collector fails on unresolved
or unowned ELF dependencies. Explicit non-ELF roots cover certificates, timezone,
NSS and OpenSSL's dynamically loaded provider. Full package status is retained
for scanning, even though installer-only dependencies are absent and this is not
an apt-installable system. No Python stdlib extension is deleted merely to hide a
CVE. The closure manifest records actual loaded-library ownership.

MLflow 2.22.5 server invokes gunicorn via an argv subprocess, not a shell
(`mlflow/server/__init__.py` -> `_exec_cmd`). Compose API/MLflow commands and
health checks also use argv. Projects/environment creation and interactive shell
sessions are not part of these production serving images. Use the builder or a
separate diagnostic container for shell-based investigation. Python exec health
checks are required in the shell-free final rootfs.

MLflow image adds pinned LightGBM 4.6.0 and libgomp1 solely to satisfy the requested
both-image native import and production-model pyfunc compatibility checks; its
existing MLflow/PyArrow versions remain unchanged. API keeps uid/gid 10001;
MLflow retains its existing user/volume behavior. Network/auth and lifecycle
remain unchanged. Runtime tests include full stdlib native imports, trust store,
timezone, all requested ML imports, serialization and pyfunc prediction, live
MLflow server health, and the existing nine-step API lifecycle run in the final
non-root API container with throwaway writable mounts. Synthetic checks establish
plumbing only. The MinIO production-stack blocker remains independent.

The build script is intentionally small; it does not promise arbitrary native
plugins loaded through ctypes/dlopen. The selected dependencies and explicit
provider roots require actual tests. Every source/base refresh must rerun the
closure, runtime and security evidence gates. Runtime or policy failure blocks
merge. Any residual OS acceptance requires a separate human decision; none is
added to the baseline here.

## First real-build correction

Run 36998564053 failed closed on `_tkinter`: the official slim base carries the
extension but lacks `libtk8.6.so` and `libtcl8.6.so`. Installing a desktop GUI
stack would expand the serving surface. The collector explicitly verifies those
exact pre-existing missing libraries and excludes only the already unusable
Tkinter extension. Every working standard-library extension, including curses,
readline and UUID, remains present. This exception is unrelated to vulnerability
counts and is recorded in the closure manifest. Unknown missing libraries still
fail the build. The default TLS paths and generated loader cache are preserved.

Artifact-derived Python version constraints prevent transitive resolver drift.
For both-image model loading, the MLflow image's numpy/pandas/sklearn/joblib pins
are aligned to the unchanged API model stack; PyArrow remains 19.0.1 in MLflow
and 17.0.0 in API, preserving the exact reviewed baseline identities. A shared
API-created model is loaded and compared in the MLflow image in remote tests.
This is a documented compatibility prerequisite, not a model-methodology change.
OS apt refresh remains an upstream mutable input; every build records actual
versions and requires runtime/scanner revalidation rather than claiming bitwise
reproducibility from a pinned base alone.

Run 36999096176 then caught an inspection-context issue: psycopg2's private
Kerberos libraries rely on the importing extension's auditwheel RPATH. A
standalone ldd of those private objects does not inherit that entrypoint RPATH.
Each build-time inspection now includes only the inspected object's sibling
library directory. No runtime LD_LIBRARY_PATH or library replacement is used;
real psycopg2/native imports and server execution remain mandatory.

Terminal runtime data (ncurses-base) and readline configuration are explicit
non-ELF roots when their corresponding CPython libraries are retained. Smoke
validation exercises curses terminfo and both UUID1/UUID4 paths, rather than
claiming support from imports alone. The vulnerable CLI executables infocmp,
nsenter and mount must be absent from both final images. This permits precise
reachability review of any conservatively source-mapped residual findings.

## Public Wolfi evidence changes the selection

The independently downloaded public `https://packages.wolfi.dev/os/x86_64/APKINDEX.tar.gz`
contains **python-3.11 and python-3.11-base 3.11.17-r0**, current glibc 2.43-r13,
ncurses 6.6.20260926-r0, libgomp/libstdc++ 16.2.0-r1, certificates 20260909-r2
and timezone data 2026e-r0. Thus the initial catalog-only conclusion about C
was incomplete: the paid ready-made image is not the only supported route.

C is now the selected candidate, pending real validation. Digest-pinned public
Wolfi plus its version-pinned Python/native packages avoids custom rootfs/ELF
maintenance, optional-extension deletions, distro compilation and unstable Debian
mixing. A matched builder/runtime venv preserves the existing pinned Python model
stack. Binary wheels only; pip check in builder and final runtime. Python
build-tool distributions are removed after installation; APK metadata remains
intact. No source/package database files are surgically deleted. The final source
diff removes the experimental D collector. A shell and APK are retained as
ordinary upstream distro components; their actual security results must pass.

The complete requested imports, native stdlib/certificates/timezone checks,
cross-image serialization/pyfunc, live MLflow, and the non-root nine-step API
lifecycle remain required. No compatibility or security count is inferred from
the Wolfi name. D runtime evidence remains useful comparison data, not the final
production strategy. Any newly unreviewed finding fails the unchanged policy.

## Independent comparison artifact rejects the apparent D green

Run 36999526356 / artifact 11223635441 passed runtime tests but its apparent
security ACCEPTED is **invalid for an OS-remediation conclusion**: Trivy metadata
says `OS.Family=none`, includes only Python results, while Syft finds retained
libuuid1/ncurses packages. Actual sizes were 957,029,758 bytes API and
935,217,269 bytes MLflow. This is a scanner-coverage defect, not zero OS risk.
D is rejected. The final evidence runner now requests all packages, requires a
recognized Debian/Wolfi OS and nonempty OS package result, and reconciles every
Syft Debian/APK package name/version against Trivy's scanned OS inventory.
Missing OS coverage or an omitted package fails closed. Six new regression cases
cover undetected OS, missing OS result, missing/mismatched inventory and Wolfi
identity support. The vulnerability baseline and acceptance identities are
unchanged. Wolfi keeps native APK databases/OS identification without rewriting.

## Wolfi runtime passed; remove unnecessary installer findings

Run 37000028810 / artifact 11223930869 passed both fresh builds, both-image
native/certificate/timezone/pyfunc/cross-image tests, live MLflow and non-root
nine-step API lifecycle. Trivy recognized Wolfi and scanned 38 APK packages,
with zero OS findings. Per image it found 26 Python findings: 22 exact reviewed
matches and four unreviewed findings, zero stale reviews. Those four originate
from pip 26.2.1's vendor.txt: setuptools 70.3.0 (CVE-2025-47273), msgpack 1.1.2
(GHSA-6v7p-g79w-8964), urllib3 2.7.0 (CVE-2026-97687, CVE-2026-97689).
Installed application urllib3 is already 2.8.0; upgrading that distribution
does not patch pip's private copies. Independently downloaded latest upstream
pip 26.2.1 confirms those exact vendor pins. No new finding is baselined.

The final runtime validates dependencies with pip check before uninstalling pip
itself using its supported uninstall operation. No runtime installer is required
for existing in-process model loading or MLflow/Gunicorn service startup. The
matched builder retains pip and performs the same checks before copying the
venv. Final smoke asserts pip/setuptools/wheel distributions are absent, then
repeats serialization/load/live server/API tests without them. pip check is
not applicable after installer removal; its successful final-layer execution
before removal is build evidence. OS-owned ensurepip/wheel files and native APK
databases are not deleted or rewritten. Actual fresh scans must establish that
this removes the active vendor findings without losing OS coverage.

OS identity reconciliation accounts for Trivy's separate Debian Epoch/Release
fields; a raw Version-only comparison would incorrectly reject valid evidence.
Explicit regressions cover full Debian and APK versions.

Both final runtimes now pin **all 38 APK package versions** from the actual
compatible Wolfi artifact, including its glibc-2.44 2.44-r7 provider. The earlier
public-index glibc 2.43 listing is not the installed provider selected by the
pinned base. The same checked-in package lock is applied to builder/runtime;
unavailable exact versions fail the build instead of silently resolving newer
packages. Together with the base digest and complete Python constraints this
makes dependency resolution reproducible. Source-repository/package availability
remains an external build dependency; refreshes require revalidation.
