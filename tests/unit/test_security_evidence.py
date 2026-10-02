from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import security_evidence


def _completed(command: list[str], returncode: int = 0, stdout: str = ""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")


@pytest.mark.parametrize(
    ("scan_returncode", "expected_status", "expected_exit"),
    [(0, "PASS", 0), (1, "FINDINGS", 1), (3, "ERROR", 2)],
)
def test_secret_scan_classifies_results_and_redacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scan_returncode: int,
    expected_status: str,
    expected_exit: int,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command, *, cwd=None):
        command = list(command)
        commands.append(command)
        if command == ["gitleaks", "version"]:
            return _completed(command, stdout="8.30.1\n")
        report_path = Path(command[command.index("--report-path") + 1])
        report_path.write_text("[]\n", encoding="utf-8")
        return _completed(command, returncode=scan_returncode)

    monkeypatch.setattr(security_evidence, "_run_command", fake_run)
    result = security_evidence.run_secret_scan(tmp_path, tmp_path / "evidence", "abc123")

    summary = json.loads((tmp_path / "evidence/gitleaks-summary.json").read_text())
    assert result == expected_exit
    assert summary["status"] == expected_status
    assert summary["source_revision"] == "abc123"
    assert commands[1][0:2] == ["gitleaks", "git"]
    assert "--log-opts=--all --full-history" in commands[1]
    assert "--redact=100" in commands[1]


def test_image_evidence_scans_immutable_ids_and_writes_spdx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []
    image_id = f"sha256:{'a' * 64}"

    def fake_run(command, *, cwd=None):
        command = list(command)
        commands.append(command)
        if command == ["trivy", "--version"]:
            return _completed(command, stdout="Version: 0.69.3\n")
        if command == ["syft", "version"]:
            return _completed(command, stdout="Version: 1.52.0\n")
        if command[0:3] == ["docker", "image", "inspect"]:
            return _completed(command, stdout=f"{image_id}\n")
        if command[0] == "trivy":
            report_path = Path(command[command.index("--output") + 1])
            report_path.write_text('{"Results": []}\n', encoding="utf-8")
            return _completed(command)
        if command[0] == "syft":
            output = next(value for value in command if value.startswith("spdx-json="))
            Path(output.removeprefix("spdx-json=")).write_text(
                '{"spdxVersion": "SPDX-2.3"}\n', encoding="utf-8"
            )
            return _completed(command)
        raise AssertionError(command)

    monkeypatch.setattr(security_evidence, "_run_command", fake_run)
    result = security_evidence.run_image_evidence(
        [security_evidence.ImageTarget("api", "ieee-fraud-api:test")],
        tmp_path,
        "abc123",
    )

    summary = json.loads((tmp_path / "images-summary.json").read_text())
    assert result == 0
    assert summary["overall_status"] == "PASS"
    assert summary["images"][0]["image_id"] == image_id
    trivy_command = next(command for command in commands if command[:2] == ["trivy", "image"])
    syft_command = next(command for command in commands if command[:2] == ["syft", "scan"])
    assert trivy_command[-1] == image_id
    assert syft_command[2] == image_id


def test_image_evidence_distinguishes_findings_from_execution_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_ids = {
        "api:test": f"sha256:{'a' * 64}",
        "mlflow:test": f"sha256:{'b' * 64}",
    }

    def fake_run(command, *, cwd=None):
        command = list(command)
        if command == ["trivy", "--version"]:
            return _completed(command, stdout="Version: 0.69.3\n")
        if command == ["syft", "version"]:
            return _completed(command, stdout="Version: 1.52.0\n")
        if command[0:3] == ["docker", "image", "inspect"]:
            return _completed(command, stdout=f"{image_ids[command[-1]]}\n")
        if command[0] == "trivy":
            report_path = Path(command[command.index("--output") + 1])
            report_path.write_text('{"Results": []}\n', encoding="utf-8")
            return _completed(
                command,
                returncode=(
                    security_evidence.TRIVY_FINDINGS_EXIT_CODE
                    if command[-1] == image_ids["api:test"]
                    else 7
                ),
            )
        if command[0] == "syft":
            output = next(value for value in command if value.startswith("spdx-json="))
            Path(output.removeprefix("spdx-json=")).write_text("{}\n", encoding="utf-8")
            return _completed(command)
        raise AssertionError(command)

    monkeypatch.setattr(security_evidence, "_run_command", fake_run)
    result = security_evidence.run_image_evidence(
        [
            security_evidence.ImageTarget("api", "api:test"),
            security_evidence.ImageTarget("mlflow", "mlflow:test"),
        ],
        tmp_path,
        "abc123",
    )

    summary = json.loads((tmp_path / "images-summary.json").read_text())
    assert result == 2
    assert summary["overall_status"] == "ERROR"
    assert summary["images"][0]["trivy"]["status"] == "FINDINGS"
    assert summary["images"][1]["trivy"]["status"] == "ERROR"
    assert all(image["sbom"]["status"] == "PASS" for image in summary["images"])


def test_wrong_tool_version_fails_closed_without_running_scans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []

    def fake_run(command, *, cwd=None):
        command = list(command)
        commands.append(command)
        return _completed(command, stdout="Version: 0.68.0\n")

    monkeypatch.setattr(security_evidence, "_run_command", fake_run)
    result = security_evidence.main(
        [
            "images",
            "--image",
            "api=api:test",
            "--output-dir",
            str(tmp_path),
            "--source-revision",
            "abc123",
        ]
    )

    assert result == 2
    assert commands == [["trivy", "--version"]]
