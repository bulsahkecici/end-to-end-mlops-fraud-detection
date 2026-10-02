#!/usr/bin/env python3
"""Run bounded, fail-closed secret, image, and SBOM evidence collection."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
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

    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
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
        _strict_json(report_path)
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


def _scan_image(target: ImageTarget, output_dir: Path) -> tuple[dict[str, Any], bool, bool]:
    record: dict[str, Any] = {
        "name": target.name,
        "requested_reference": target.reference,
        "image_id": None,
        "trivy": {"report": f"{target.name}-trivy.json", "status": "ERROR"},
        "sbom": {
            "format": "spdx-json",
            "report": f"{target.name}-sbom.spdx.json",
            "status": "ERROR",
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
            image_id,
        ]
    )
    trivy_findings = trivy_result.returncode == TRIVY_FINDINGS_EXIT_CODE
    trivy_error = trivy_result.returncode not in {0, TRIVY_FINDINGS_EXIT_CODE}
    if not trivy_error:
        try:
            _strict_json(trivy_path)
        except EvidenceError:
            trivy_error = True
            trivy_findings = False
    record["trivy"]["status"] = "ERROR" if trivy_error else "FINDINGS" if trivy_findings else "PASS"

    syft_result = _run_command(["syft", "scan", image_id, "--output", f"spdx-json={sbom_path}"])
    syft_error = syft_result.returncode != 0
    if not syft_error:
        try:
            _strict_json(sbom_path)
        except EvidenceError:
            syft_error = True
    record["sbom"]["status"] = "ERROR" if syft_error else "PASS"

    print(
        f"Image {target.name}: Trivy={record['trivy']['status']}, "
        f"SBOM={record['sbom']['status']}."
    )
    return record, trivy_findings, trivy_error or syft_error


def run_image_evidence(
    images: Sequence[ImageTarget], output_dir: Path, source_revision: str
) -> int:
    """Scan exact local image IDs and generate SPDX JSON SBOMs for each."""

    summary_path = output_dir / "images-summary.json"
    _prepare_output(summary_path)
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
        record, findings, error = _scan_image(target, output_dir)
        records.append(record)
        any_findings = any_findings or findings
        any_error = any_error or error

    overall_status = "ERROR" if any_error else "FINDINGS" if any_findings else "PASS"
    _write_json(
        summary_path,
        {
            "schema_version": "1.0",
            "source_revision": source_revision,
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
    return 2 if any_error else 1 if any_findings else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    secrets = subparsers.add_parser("secrets", help="scan full reachable Git history")
    secrets.add_argument("--repository", type=Path, required=True)
    secrets.add_argument("--output-dir", type=Path, required=True)
    secrets.add_argument("--source-revision", required=True)

    images = subparsers.add_parser("images", help="scan images and generate SPDX JSON SBOMs")
    images.add_argument("--image", action="append", type=_parse_image, required=True)
    images.add_argument("--output-dir", type=Path, required=True)
    images.add_argument("--source-revision", required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    try:
        if args.command == "secrets":
            return run_secret_scan(args.repository, args.output_dir, args.source_revision)
        return run_image_evidence(args.image, args.output_dir, args.source_revision)
    except EvidenceError as exc:
        print(f"Security evidence ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
