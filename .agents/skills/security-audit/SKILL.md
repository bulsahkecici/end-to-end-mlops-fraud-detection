---
name: security-audit
description: Audit Python dependencies, container images, SBOMs, and secret exposure for this repository without unsafe automatic upgrades.
---

# Security audit

- Run `pip-audit` against pinned runtime and development requirements when available. Report the command, database/tool version, affected package, severity or advisory, installed/fixed versions, and whether the dependency is runtime-reachable.
- Build the relevant image and scan it with Trivy when available. An equivalent configured scanner may supplement, but not silently replace, requested Trivy coverage.
- Generate or inspect an SBOM with an available tool such as Syft or `docker sbom`; record the image digest and tool used. If tooling is unavailable, report the gap rather than claiming coverage or installing software without authorization.
- Check tracked files, history as appropriate, Compose/env examples, logs, artifacts, and generated reports for exposed secrets. Prefer a dedicated scanner such as Gitleaks when available. Never print secret values; report location and secret class with redaction.
- Review direct pinning, transitive dependency drift, stale base images, unpinned image tags, and platform compatibility risks.
- Triage findings by severity, exploitability/reachability, available fix, and breaking-change risk. Do not blindly upgrade major versions or incompatible dependencies; propose and verify scoped remediations separately.
- Distinguish confirmed findings, accepted/documented risks, false positives, and checks not run.
