#!/usr/bin/env python3
"""Run bounded, fail-closed secret, image, and SBOM evidence collection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

GITLEAKS_VERSION = "8.30.1"
TRIVY_VERSION = "0.69.3"
SYFT_VERSION = "1.52.0"
TRIVY_FINDINGS_EXIT_CODE = 20
IMAGE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class EvidenceError(RuntimeError):
    """A controlled security-evidence execution failure."""


@dataclass(frozen=True)
class ImageTarget:
    name: str
    reference: str


def _run_command(
    command: Sequence[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise EvidenceError(f"required executable is unavailable: {command[0]}") from exc


def _reported_version(executable: str, arguments: Sequence[str], expected: str) -> str:
    result = _run_command([executable, *arguments])
    if result.returncode != 0:
        raise EvidenceError(f"{executable} version check failed")
    output = " ".join(f"{result.stdout}\n{result.stderr}".split())
    if not re.search(rf"(?<!\d){re.escape(expected)}(?!\d)", output):
        raise EvidenceError(f"{executable} is not the required version {expected}")
    return output[:500]


def _strict_json(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"invalid or unavailable JSON evidence: {path.name}") from exc


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _prepare_output(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)


def run_secret_scan(repository: Path, output_dir: Path, source_revision: str) -> int:
    """Scan the full reachable Git history, with secrets redacted from evidence."""

    report_path = output_dir / "gitleaks.json"
    summary_path = output_dir / "gitleaks-summary.json"
    _prepare_output(report_path)
    _prepare_output(summary_path)
    version = _reported_version("gitleaks", ["version"], GITLEAKS_VERSION)
    result = _run_command(
        [
            "gitleaks",
            "git",
            "--log-opts=--all --full-history",
            "--redact=100",
            "--no-banner",
            "--report-format",
            "json",
            "--report-path",
            str(report_path),
            str(repository.resolve()),
        ],
        cwd=repository.resolve(),
    )

    if result.returncode == 0:
        status = "PASS"
        exit_code = 0
    elif result.returncode == 1:
        status = "FINDINGS"
        exit_code = 1
    else:
        status = "ERROR"
        exit_code = 2

    if report_path.exists():
        report = _strict_json(report_path)
        if (
            not isinstance(report, list)
            or any(not isinstance(finding, dict) for finding in report)
            or (status != "ERROR" and bool(report) != (status == "FINDINGS"))
        ):
            status = "ERROR"
            exit_code = 2
    elif status != "ERROR":
        status = "ERROR"
        exit_code = 2

    _write_json(
        summary_path,
        {
            "schema_version": "1.0",
            "source_revision": source_revision,
            "scope": "full reachable Git history in the checked-out repository",
            "redaction_percent": 100,
            "report": report_path.name,
            "status": status,
            "tool": {
                "name": "gitleaks",
                "required_version": GITLEAKS_VERSION,
                "reported_version": version,
            },
        },
    )
    print(f"Gitleaks evidence status: {status}; report values are fully redacted.")
    return exit_code


def _parse_image(value: str) -> ImageTarget:
    name, separator, reference = value.partition("=")
    if not separator or not IMAGE_NAME_PATTERN.fullmatch(name) or not reference:
        raise argparse.ArgumentTypeError("image must use a safe NAME=REFERENCE value")
    return ImageTarget(name=name, reference=reference)


def _image_id(reference: str) -> str:
    result = _run_command(["docker", "image", "inspect", "--format={{.Id}}", reference])
    image_id = result.stdout.strip()
    if result.returncode != 0 or not IMAGE_ID_PATTERN.fullmatch(image_id):
        raise EvidenceError(f"could not resolve an immutable local image ID for {reference}")
    return image_id


# Exact identities include severity so a severity change also requires review.
BASELINE_PATH = (
    Path(__file__).resolve().parent.parent / "security/container_vulnerability_baseline.json"
)
IDENTITY_FIELDS = ("image_role", "vulnerability_id", "package", "installed_version", "severity")


def _identity(entry: dict[str, Any]) -> tuple[str, ...]:
    return tuple(entry[field] for field in IDENTITY_FIELDS)


def _exact_string(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and not any(character in value for character in "*?[]")
    )


def load_baseline(path: Path) -> list[dict[str, Any]]:
    """Validate the complete baseline, including duplicate identities and dates."""
    payload = _strict_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
        raise EvidenceError("invalid container baseline schema")
    entries = payload.get("accepted_findings")
    if not isinstance(entries, list):
        raise EvidenceError("container baseline entries must be a list")
    seen: set[tuple[str, ...]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not all(
            _exact_string(entry.get(field)) for field in IDENTITY_FIELDS
        ):
            raise EvidenceError("container baseline requires exact identities without wildcards")
        if not IMAGE_NAME_PATTERN.fullmatch(entry["image_role"]):
            raise EvidenceError("invalid baseline image role")
        if entry["severity"] not in {"HIGH", "CRITICAL"}:
            raise EvidenceError("invalid baseline severity")
        if not isinstance(entry.get("rationale"), str) or not entry["rationale"].strip():
            raise EvidenceError("baseline rationale is required")
        if not isinstance(entry.get("fixed_version_reported"), str):
            raise EvidenceError("baseline fixed version snapshot is required")
        try:
            dates = []
            for field in ("reviewed_on", "expires_on"):
                value = entry[field]
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError("invalid date format")
                dates.append(date.fromisoformat(value))
            if dates[0] >= dates[1]:
                raise ValueError("expiry must follow review")
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceError("invalid baseline review/expiry dates") from exc
        identity = _identity(entry)
        if identity in seen:
            raise EvidenceError("duplicate container baseline identity")
        seen.add(identity)
    return sorted(entries, key=_identity)


def evaluate_report(
    payload: Any,
    image_role: str,
    baseline: list[dict[str, Any]],
    *,
    today: date | None = None,
    image_id: str | None = None,
) -> dict[str, Any]:
    """Fail closed on finding drift, expired reviews, or any fix snapshot change.

    A changed fix snapshot always requires review, including new compatible
    fixes. This deliberately avoids guessing compatibility from version numbers.
    Stale entries fail closed until the reviewed baseline is reconciled.
    """
    today = today or datetime.now(UTC).date()
    if (
        not isinstance(payload, dict)
        or payload.get("SchemaVersion") != 2
        or not isinstance(payload.get("Results"), list)
    ):
        raise EvidenceError("malformed Trivy report schema")
    if image_id is not None and (
        payload.get("ArtifactName") != image_id
        or not isinstance(payload.get("Metadata"), dict)
        or payload["Metadata"].get("ImageID") != image_id
    ):
        raise EvidenceError("Trivy report image identity mismatch")
    findings: dict[tuple[str, ...], dict[str, Any]] = {}
    for result in payload["Results"]:
        if not isinstance(result, dict):
            raise EvidenceError("malformed Trivy result")
        vulnerabilities = result.get("Vulnerabilities", [])
        if not isinstance(vulnerabilities, list):
            raise EvidenceError("malformed Trivy vulnerabilities")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                raise EvidenceError("malformed Trivy finding")
            entry = {"image_role": image_role}
            for target, source in (
                ("vulnerability_id", "VulnerabilityID"),
                ("package", "PkgName"),
                ("installed_version", "InstalledVersion"),
                ("severity", "Severity"),
            ):
                value = vulnerability.get(source)
                if not _exact_string(value):
                    raise EvidenceError("malformed Trivy finding identity")
                entry[target] = value
            if entry["severity"] not in {"HIGH", "CRITICAL"}:
                raise EvidenceError("unexpected severity in bounded Trivy scan")
            fixed = vulnerability.get("FixedVersion", "")
            if not isinstance(fixed, str):
                raise EvidenceError("malformed Trivy fixed version")
            entry["fixed_version_reported"] = fixed
            identity = _identity(entry)
            if identity in findings and findings[identity] != entry:
                raise EvidenceError("conflicting duplicate Trivy finding")
            findings[identity] = entry
    accepted = {_identity(entry): entry for entry in baseline if entry["image_role"] == image_role}
    issues: list[dict[str, Any]] = []
    matched = 0
    for identity, entry in sorted(accepted.items()):
        if date.fromisoformat(entry["expires_on"]) <= today:
            issues.append({"reason": "expired", "identity": list(identity)})
        if date.fromisoformat(entry["reviewed_on"]) > today:
            issues.append({"reason": "future_review", "identity": list(identity)})
    for identity, finding in sorted(findings.items()):
        reviewed = accepted.get(identity)
        if reviewed is None:
            issues.append({"reason": "unreviewed", "identity": list(identity)})
        elif finding["fixed_version_reported"] != reviewed["fixed_version_reported"]:
            issues.append({"reason": "fixed_version_changed", "identity": list(identity)})
        else:
            matched += 1
    stale = [list(identity) for identity in sorted(accepted.keys() - findings.keys())]
    issues.extend({"reason": "stale", "identity": identity} for identity in stale)
    return {
        "status": "FAIL" if issues else "ACCEPTED" if findings else "PASS",
        "finding_count": len(findings),
        "matched_count": matched,
        "issues": issues,
        "stale_baseline_entries": stale,
        "stale_baseline_requires_review": bool(stale),
    }


def validate_os_coverage(report: Any, sbom: Any) -> dict[str, Any]:
    """Reject scanner blind spots and reconcile installed OS packages with Syft."""
    metadata = report.get("Metadata", {}) if isinstance(report, dict) else {}
    operating_system = metadata.get("OS", {})
    if (
        not isinstance(operating_system, dict)
        or operating_system.get("Family") not in {"debian", "wolfi"}
        or not _exact_string(operating_system.get("Name"))
    ):
        raise EvidenceError("Trivy did not recognize the expected Debian/Wolfi OS")
    scanned = {
        (package.get("Name"), package.get("Version"))
        for result in report.get("Results", [])
        if result.get("Class") == "os-pkgs"
        for package in result.get("Packages", [])
        if isinstance(package, dict)
    }
    inventory = {
        (package["name"], package.get("versionInfo"))
        for package in sbom["packages"]
        if any(
            reference.get("referenceType") == "purl"
            and str(reference.get("referenceLocator", "")).startswith(("pkg:deb/", "pkg:apk/"))
            for reference in package.get("externalRefs", [])
            if isinstance(reference, dict)
        )
    }
    if (
        not scanned
        or not inventory
        or any(not _exact_string(name) or not _exact_string(version) for name, version in inventory)
    ):
        raise EvidenceError("Missing OS package inventory in Trivy or Syft")
    missing = inventory - scanned
    if missing:
        raise EvidenceError(f"Trivy omitted Syft OS package identities: {sorted(missing)}")
    return {
        "status": "PASS",
        "family": operating_system["Family"],
        "trivy_package_count": len(scanned),
        "syft_package_count": len(inventory),
    }


def _scan_image(
    target: ImageTarget, output_dir: Path, baseline: list[dict[str, Any]]
) -> tuple[dict[str, Any], bool, bool]:
    record: dict[str, Any] = {
        "name": target.name,
        "requested_reference": target.reference,
        "image_id": None,
        "status": "FAIL",
        "trivy": {"report": f"{target.name}-trivy.json", "status": "FAIL"},
        "sbom": {
            "format": "spdx-json",
            "report": f"{target.name}-sbom.spdx.json",
            "status": "FAIL",
        },
    }
    try:
        image_id = _image_id(target.reference)
    except EvidenceError:
        return record, False, True
    record["image_id"] = image_id

    trivy_path = output_dir / record["trivy"]["report"]
    sbom_path = output_dir / record["sbom"]["report"]
    _prepare_output(trivy_path)
    _prepare_output(sbom_path)

    trivy_result = _run_command(
        [
            "trivy",
            "image",
            "--scanners",
            "vuln",
            "--severity",
            "HIGH,CRITICAL",
            "--format",
            "json",
            "--output",
            str(trivy_path),
            "--exit-code",
            str(TRIVY_FINDINGS_EXIT_CODE),
            "--no-progress",
            "--list-all-pkgs",
            image_id,
        ]
    )
    trivy_findings = trivy_result.returncode == TRIVY_FINDINGS_EXIT_CODE
    trivy_error = trivy_result.returncode not in {0, TRIVY_FINDINGS_EXIT_CODE}
    if not trivy_error:
        try:
            trivy_payload = _strict_json(trivy_path)
            evaluation = evaluate_report(trivy_payload, target.name, baseline, image_id=image_id)
            if bool(evaluation["finding_count"]) != trivy_findings:
                raise EvidenceError("Trivy exit code disagrees with findings")
            record["baseline_evaluation"] = evaluation
        except EvidenceError:
            trivy_error = True
            trivy_findings = False
    record["trivy"]["status"] = "FAIL" if trivy_error else evaluation["status"]
    policy_failure = trivy_error or evaluation["status"] == "FAIL"

    syft_result = _run_command(["syft", "scan", image_id, "--output", f"spdx-json={sbom_path}"])
    syft_error = syft_result.returncode != 0
    if not syft_error:
        try:
            sbom = _strict_json(sbom_path)
            if (
                not isinstance(sbom, dict)
                or sbom.get("spdxVersion") != "SPDX-2.3"
                or sbom.get("SPDXID") != "SPDXRef-DOCUMENT"
                or sbom.get("dataLicense") != "CC0-1.0"
                or not _exact_string(sbom.get("documentNamespace"))
                or not isinstance(sbom.get("creationInfo"), dict)
                or not isinstance(sbom.get("packages"), list)
                or not sbom["packages"]
                or any(
                    not isinstance(package, dict)
                    or not _exact_string(package.get("name"))
                    or not _exact_string(package.get("SPDXID"))
                    for package in sbom["packages"]
                )
            ):
                raise EvidenceError("invalid SPDX JSON SBOM")
        except EvidenceError:
            syft_error = True
    record["sbom"]["status"] = "FAIL" if syft_error else "PASS"
    if not trivy_error and not syft_error:
        try:
            record["os_coverage"] = validate_os_coverage(trivy_payload, sbom)
        except EvidenceError as exc:
            record["os_coverage"] = {"status": "FAIL", "error": str(exc)}
            record["trivy"]["status"] = "FAIL"
            policy_failure = True

    print(
        f"Image {target.name}: Trivy={record['trivy']['status']}, "
        f"SBOM={record['sbom']['status']}."
    )
    record["status"] = "FAIL" if policy_failure or syft_error else record["trivy"]["status"]
    return record, record["status"] == "ACCEPTED", policy_failure or syft_error


def run_image_evidence(
    images: Sequence[ImageTarget],
    output_dir: Path,
    source_revision: str,
    baseline_path: Path | None = None,
) -> int:
    """Scan exact local image IDs and generate SPDX JSON SBOMs for each."""

    summary_path = output_dir / "images-summary.json"
    _prepare_output(summary_path)
    baseline = load_baseline(baseline_path) if baseline_path is not None else []
    if not images or len({target.name for target in images}) != len(images):
        raise EvidenceError("image roles must be nonempty and unique")
    tool_versions = {
        "trivy": {
            "required_version": TRIVY_VERSION,
            "reported_version": _reported_version("trivy", ["--version"], TRIVY_VERSION),
        },
        "syft": {
            "required_version": SYFT_VERSION,
            "reported_version": _reported_version("syft", ["version"], SYFT_VERSION),
        },
    }

    records: list[dict[str, Any]] = []
    any_findings = False
    any_error = False
    for target in images:
        record, findings, error = _scan_image(target, output_dir, baseline)
        records.append(record)
        any_findings = any_findings or findings
        any_error = any_error or error

    overall_status = "FAIL" if any_error else "ACCEPTED" if any_findings else "PASS"
    _write_json(
        summary_path,
        {
            "schema_version": "1.1",
            "source_revision": source_revision,
            "baseline_sha256": (
                hashlib.sha256(baseline_path.read_bytes()).hexdigest()
                if baseline_path is not None
                else None
            ),
            "overall_status": overall_status,
            "policy": {
                "scanner": "vulnerability",
                "severities": ["HIGH", "CRITICAL"],
                "include_unfixed": True,
                "finding_exit_code": TRIVY_FINDINGS_EXIT_CODE,
                "sbom_format": "spdx-json",
            },
            "tools": tool_versions,
            "images": records,
        },
    )
    print(f"Container security evidence status: {overall_status}.")
    return 2 if any_error else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    secrets = subparsers.add_parser("secrets", help="scan full reachable Git history")
    secrets.add_argument("--repository", type=Path, required=True)
    secrets.add_argument("--output-dir", type=Path, required=True)
    secrets.add_argument("--source-revision", required=True)

    images = subparsers.add_parser("images", help="scan images and generate SPDX JSON SBOMs")
    images.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    images.add_argument("--image", action="append", type=_parse_image, required=True)
    images.add_argument("--output-dir", type=Path, required=True)
    images.add_argument("--source-revision", required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    try:
        if args.command == "secrets":
            return run_secret_scan(args.repository, args.output_dir, args.source_revision)
        return run_image_evidence(args.image, args.output_dir, args.source_revision, args.baseline)
    except EvidenceError as exc:
        if args.command == "images":
            _write_json(
                args.output_dir / "images-summary.json",
                {
                    "schema_version": "1.1",
                    "source_revision": args.source_revision,
                    "overall_status": "FAIL",
                    "error": str(exc),
                },
            )
        print(f"Security evidence ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
