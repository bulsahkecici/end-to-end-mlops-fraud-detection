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
