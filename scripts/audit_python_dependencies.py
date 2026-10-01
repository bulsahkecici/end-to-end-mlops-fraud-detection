"""Audit pinned Python dependencies against an explicit reviewed baseline.

The command fails for new findings, removed findings, newly available
same-major fixes, collection errors, or malformed output. It never prints
advisory descriptions or environment values.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = PROJECT_ROOT / "requirements-dev.txt"

# Reviewed 2026-10-01. MLflow has no compatible 2.x fixes for these findings;
# PyArrow's Python bindings do not expose the vulnerable pre-buffering API.
ACCEPTED_FINDINGS = frozenset(
    {
        ("mlflow", "2.22.5", "GHSA-gqvg-gmmx-x4hm"),
        ("mlflow", "2.22.5", "PYSEC-2025-52"),
        ("mlflow", "2.22.5", "PYSEC-2026-93"),
        ("mlflow", "2.22.5", "PYSEC-2026-94"),
        ("mlflow", "2.22.5", "PYSEC-2026-195"),
        ("mlflow", "2.22.5", "PYSEC-2026-419"),
        ("mlflow", "2.22.5", "PYSEC-2026-421"),
        ("mlflow", "2.22.5", "PYSEC-2026-423"),
        ("mlflow", "2.22.5", "PYSEC-2026-424"),
        ("mlflow", "2.22.5", "PYSEC-2026-425"),
        ("mlflow", "2.22.5", "PYSEC-2026-1639"),
        ("mlflow", "2.22.5", "PYSEC-2026-1656"),
        ("mlflow", "2.22.5", "PYSEC-2026-1660"),
        ("mlflow", "2.22.5", "PYSEC-2026-2219"),
        ("mlflow", "2.22.5", "PYSEC-2026-2220"),
        ("mlflow", "2.22.5", "PYSEC-2026-2221"),
        ("mlflow", "2.22.5", "PYSEC-2026-2222"),
        ("mlflow", "2.22.5", "PYSEC-2026-2654"),
        ("mlflow", "2.22.5", "PYSEC-2026-2655"),
        ("mlflow", "2.22.5", "PYSEC-2026-2656"),
        ("mlflow", "2.22.5", "PYSEC-2026-2657"),
        ("mlflow", "2.22.5", "PYSEC-2026-2658"),
        ("mlflow", "2.22.5", "PYSEC-2026-2659"),
        ("mlflow", "2.22.5", "PYSEC-2026-2660"),
        ("mlflow", "2.22.5", "PYSEC-2026-2661"),
        ("mlflow", "2.22.5", "PYSEC-2026-3686"),
        ("mlflow", "2.22.5", "PYSEC-2026-3687"),
        ("pyarrow", "17.0.0", "PYSEC-2026-113"),
    }
)


def _findings(payload: dict[str, Any]) -> frozenset[tuple[str, str, str]]:
    findings: set[tuple[str, str, str]] = set()
    for dependency in payload.get("dependencies", []):
        name = str(dependency["name"]).lower()
        version = str(dependency["version"])
        for vulnerability in dependency.get("vulns", []):
            findings.add((name, version, str(vulnerability["id"])))
    return frozenset(findings)


def _compatible_major_fixes(payload: dict[str, Any]) -> frozenset[tuple[str, str, str, str]]:
    fixes: set[tuple[str, str, str, str]] = set()
    for dependency in payload.get("dependencies", []):
        name = str(dependency["name"]).lower()
        version = str(dependency["version"])
        installed_major = version.partition(".")[0]
        for vulnerability in dependency.get("vulns", []):
            advisory = str(vulnerability["id"])
            for fixed_version in vulnerability.get("fix_versions", []):
                fixed_version = str(fixed_version)
                if fixed_version.partition(".")[0] == installed_major:
                    fixes.add((name, version, advisory, fixed_version))
    return frozenset(fixes)


def main() -> int:
    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "--requirement",
        str(REQUIREMENTS),
        "--format",
        "json",
        "--desc",
        "off",
        "--aliases",
        "off",
        "--progress-spinner",
        "off",
        "--strict",
    ]
    result = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, check=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("pip-audit failed without valid JSON output", file=sys.stderr)
        return 2

    findings = _findings(payload)
    unexpected = findings - ACCEPTED_FINDINGS
    stale = ACCEPTED_FINDINGS - findings
    compatible_fixes = _compatible_major_fixes(payload)
    if unexpected or stale or compatible_fixes:
        for prefix, entries in (("unexpected", unexpected), ("stale", stale)):
            for package, version, advisory in sorted(entries):
                print(f"{prefix}: {package}=={version} {advisory}", file=sys.stderr)
        for package, version, advisory, fixed_version in sorted(compatible_fixes):
            print(
                f"compatible fix available: {package}=={version} {advisory} -> {fixed_version}",
                file=sys.stderr,
            )
        return 1
    if result.returncode not in {0, 1}:
        print(f"pip-audit collection failed with exit code {result.returncode}", file=sys.stderr)
        return 2

    print(
        f"pip-audit reviewed baseline matched: {len(findings)} unique advisories "
        f"across {len({package for package, _, _ in findings})} packages"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
