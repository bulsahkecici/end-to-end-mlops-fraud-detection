from __future__ import annotations

import json
import subprocess
from datetime import date
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
        report_path.write_text(json.dumps([{}] if scan_returncode == 1 else []), encoding="utf-8")
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
            report_path.write_text(json.dumps(_report([], command[-1])), encoding="utf-8")
            return _completed(command)
        if command[0] == "syft":
            output = next(value for value in command if value.startswith("spdx-json="))
            Path(output.removeprefix("spdx-json=")).write_text(
                json.dumps(_sbom()), encoding="utf-8"
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
    assert "--list-all-pkgs" in trivy_command
    assert summary["images"][0]["os_coverage"]["status"] == "PASS"
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
            report_path.write_text(
                json.dumps(_report([_vulnerability()], command[-1])), encoding="utf-8"
            )
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
            Path(output.removeprefix("spdx-json=")).write_text(
                json.dumps(_sbom()), encoding="utf-8"
            )
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
    assert summary["overall_status"] == "FAIL"
    assert summary["images"][0]["trivy"]["status"] == "FAIL"
    assert summary["images"][0]["baseline_evaluation"]["issues"][0]["reason"] == "unreviewed"
    assert summary["images"][1]["trivy"]["status"] == "FAIL"
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


TODAY = date(2026, 10, 2)


def _entry(**changes):
    entry = {
        "image_role": "api",
        "vulnerability_id": "CVE-2026-25087",
        "package": "pyarrow",
        "installed_version": "17.0.0",
        "severity": "HIGH",
        "fixed_version_reported": "23.0.1",
        "rationale": "Fix conflicts with MLflow pyarrow<20 constraint.",
        "reviewed_on": "2026-10-02",
        "expires_on": "2026-11-01",
    }
    return entry | changes


def _vulnerability(**changes):
    return {
        "VulnerabilityID": "CVE-2026-25087",
        "PkgName": "pyarrow",
        "InstalledVersion": "17.0.0",
        "Severity": "HIGH",
        "FixedVersion": "23.0.1",
    } | changes


def _report(vulnerabilities, image_id=None):
    return {
        "SchemaVersion": 2,
        "ArtifactName": image_id,
        "Metadata": {"ImageID": image_id, "OS": {"Family": "debian", "Name": "13.7"}},
        "Results": [
            {"Class": "os-pkgs", "Packages": [{"Name": "libc6", "Version": "2.41-12"}]},
            {"Class": "lang-pkgs", "Vulnerabilities": vulnerabilities},
        ],
    }


def _evaluate(vulnerabilities, entries=None, role="api"):
    return security_evidence.evaluate_report(
        _report(vulnerabilities), role, [_entry()] if entries is None else entries, today=TODAY
    )


def _load(tmp_path, entries):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"schema_version": "1.0", "accepted_findings": entries}))
    return security_evidence.load_baseline(path)


def test_exact_residual_accepted_and_no_findings_pass():
    result = _evaluate([_vulnerability()])
    assert result["status"] == "ACCEPTED"
    assert result["matched_count"] == 1
    assert _evaluate([], [])["status"] == "PASS"


@pytest.mark.parametrize(
    "changes",
    [
        {"PkgName": "other"},
        {"InstalledVersion": "19.0.1"},
        {"VulnerabilityID": "CVE-2026-99999"},
        {"Severity": "CRITICAL"},
    ],
)
def test_changed_identity_fails(changes):
    assert _evaluate([_vulnerability(**changes)])["status"] == "FAIL"


def test_image_roles_and_versions_evaluated_independently():
    entries = [_entry(), _entry(image_role="mlflow", installed_version="19.0.1")]
    assert _evaluate([_vulnerability()], entries, "api")["status"] == "ACCEPTED"
    assert _evaluate([_vulnerability()], entries, "mlflow")["status"] == "FAIL"
    assert (
        _evaluate([_vulnerability(InstalledVersion="19.0.1")], entries, "mlflow")["status"]
        == "ACCEPTED"
    )
    assert _evaluate([_vulnerability()], entries, "unknown")["status"] == "FAIL"


@pytest.mark.parametrize("changes", [{"expires_on": "2026-10-02"}, {"reviewed_on": "2026-10-03"}])
def test_expired_or_future_review_fails(changes):
    assert _evaluate([_vulnerability()], [_entry(**changes)])["status"] == "FAIL"


def test_new_finding_fails_and_stale_entry_is_flagged():
    result = _evaluate([_vulnerability(), _vulnerability(VulnerabilityID="CVE-2026-99999")])
    assert result["status"] == "FAIL"
    stale = _evaluate([])
    assert stale["status"] == "FAIL"
    assert stale["stale_baseline_requires_review"]
    assert stale["stale_baseline_entries"] == [list(security_evidence._identity(_entry()))]


@pytest.mark.parametrize("fixed", ["19.0.2", "", "23.0.2"])
def test_changed_fix_snapshot_requires_review(fixed):
    result = _evaluate([_vulnerability(FixedVersion=fixed)])
    assert result["status"] == "FAIL"
    assert result["issues"][0]["reason"] == "fixed_version_changed"


def test_output_order_and_identical_duplicates_do_not_change_decision():
    second_entry = _entry(vulnerability_id="CVE-2026-99999")
    second_finding = _vulnerability(VulnerabilityID="CVE-2026-99999")
    expected = _evaluate([_vulnerability(), second_finding], [_entry(), second_entry])
    assert expected == _evaluate(
        [second_finding, _vulnerability(), second_finding], [second_entry, _entry()]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"severity": "LOW"},
        {"severity": "high"},
        {"image_role": "*"},
        {"package": "py*"},
        {"vulnerability_id": "CVE-*"},
        {"installed_version": "17.?"},
        {"rationale": ""},
        {"fixed_version_reported": None},
        {"reviewed_on": "2026-02-30"},
        {"reviewed_on": "20261002"},
        {"reviewed_on": "2026-11-02"},
        {"expires_on": None},
    ],
)
def test_baseline_validation_rejects_invalid_entries(tmp_path, changes):
    with pytest.raises(security_evidence.EvidenceError):
        _load(tmp_path, [_entry(**changes)])


def test_baseline_dates_and_duplicate_identities(tmp_path):
    assert _load(tmp_path, [_entry()]) == [_entry()]
    with pytest.raises(security_evidence.EvidenceError, match="duplicate"):
        _load(tmp_path, [_entry(), _entry()])


@pytest.mark.parametrize(
    "content",
    [
        "{",
        "[]",
        "{}",
        '{"schema_version":"1.0","accepted_findings":{}}',
        '{"schema_version":"1.0","schema_version":"1.0","accepted_findings":[]}',
    ],
)
def test_malformed_baseline_json_fails(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(security_evidence.EvidenceError):
        security_evidence.load_baseline(path)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"SchemaVersion": 2, "Results": {}},
        {"SchemaVersion": 2, "Results": [None]},
        {"SchemaVersion": 2, "Results": [{"Vulnerabilities": None}]},
        _report([{}]),
        _report([_vulnerability(FixedVersion=None)]),
    ],
)
def test_malformed_trivy_structure_fails(payload):
    with pytest.raises(security_evidence.EvidenceError):
        security_evidence.evaluate_report(payload, "api", [], today=TODAY)


def test_image_identity_mismatch_fails():
    with pytest.raises(security_evidence.EvidenceError, match="identity"):
        security_evidence.evaluate_report(_report([], "wrong"), "api", [], image_id="expected")


@pytest.mark.parametrize(
    "failure",
    [
        "scanner",
        "sbom",
        "malformed_sbom",
        "malformed",
        "identity",
        "exit_mismatch",
        "image_resolution",
        "undetected_os",
        "missing_os_result",
        "missing_os_package",
        "none",
    ],
)
def test_execution_errors_never_become_accepted(tmp_path, monkeypatch, failure):
    image_id = f"sha256:{'a' * 64}"
    baseline = tmp_path / "baseline.json"
    _load(tmp_path, [_entry(reviewed_on="2020-01-01", expires_on="2099-01-01")])

    def fake_run(command, *, cwd=None):
        command = list(command)
        if command == ["trivy", "--version"]:
            return _completed(command, stdout="0.69.3")
        if command == ["syft", "version"]:
            return _completed(command, stdout="1.52.0")
        if command[:3] == ["docker", "image", "inspect"]:
            return _completed(
                command, stdout="invalid" if failure == "image_resolution" else image_id
            )
        if command[0] == "trivy":
            path = Path(command[command.index("--output") + 1])
            payload = _report([_vulnerability()], "wrong" if failure == "identity" else image_id)
            if failure == "undetected_os":
                payload["Metadata"]["OS"] = {"Family": "none", "Name": ""}
            elif failure == "missing_os_result":
                payload["Results"] = payload["Results"][1:]
            elif failure == "missing_os_package":
                payload["Results"][0]["Packages"] = [{"Name": "libc6", "Version": "wrong"}]
            path.write_text("{" if failure == "malformed" else json.dumps(payload))
            code = 7 if failure == "scanner" else 0 if failure == "exit_mismatch" else 20
            return _completed(command, returncode=code)
        output = next(value for value in command if value.startswith("spdx-json="))
        Path(output.removeprefix("spdx-json=")).write_text(
            "{}" if failure == "malformed_sbom" else json.dumps(_sbom())
        )
        return _completed(command, returncode=7 if failure == "sbom" else 0)

    monkeypatch.setattr(security_evidence, "_run_command", fake_run)
    assert security_evidence.run_image_evidence(
        [security_evidence.ImageTarget("api", "api:test")], tmp_path, "rev", baseline
    ) == (0 if failure == "none" else 2)
    summary = json.loads((tmp_path / "images-summary.json").read_text())
    assert summary["overall_status"] == ("ACCEPTED" if failure == "none" else "FAIL")


def test_runtime_build_tool_policy():
    root = Path(__file__).resolve().parents[2]
    package_pins = (root / "docker/wolfi.packages").read_text()
    for filename in ("Dockerfile.api", "Dockerfile.mlflow"):
        contents = (root / filename).read_text()
        builder, runtime = contents.rsplit("FROM ", 1)
        assert "wolfi-base:latest@sha256:" in runtime
        assert "COPY docker/wolfi.packages /packages" in runtime
        assert "RUN xargs apk add --no-cache < /packages" in runtime
        assert "python-3.11=3.11.17-r0" in package_pins
        assert "libgomp=16.2.0-r1" in package_pins
        assert "libstdc++=16.2.0-r1" in package_pins
        assert "ca-certificates-bundle=20260909-r2" in package_pins
        assert "tzdata=2026e-r0" in package_pins
        assert "python -m pip uninstall -y setuptools wheel && python -m pip check" in builder
        assert builder.index("pip uninstall -y setuptools wheel") > builder.index("pip install")
        assert "--only-binary=:all:" in builder
        assert "COPY --from=builder /opt/venv /opt/venv" in runtime
        assert "RUN python -m pip check && python -m pip uninstall -y pip" in runtime
        assert "build-essential" not in contents
    assert "pyarrow==17.0.0" in (root / "requirements.txt").read_text()


def test_checked_in_baseline_has_only_exact_mlflow_pyarrow_residuals():
    entries = security_evidence.load_baseline(security_evidence.BASELINE_PATH)
    assert len(entries) == 44
    assert {entry["package"] for entry in entries} == {"mlflow", "pyarrow"}
    assert {entry["image_role"] for entry in entries} == {"api", "mlflow"}
    assert all(
        entry["installed_version"] == "2.22.5" for entry in entries if entry["package"] == "mlflow"
    )


def test_mlflow_environment_metadata_tolerates_missing_build_tools(monkeypatch):
    from importlib import metadata

    from mlflow.utils.environment import _PythonEnv

    original_version = metadata.version

    def without_build_tools(package):
        if package in {"setuptools", "wheel"}:
            raise metadata.PackageNotFoundError(package)
        return original_version(package)

    monkeypatch.setattr(metadata, "version", without_build_tools)
    dependencies = _PythonEnv.current().build_dependencies
    assert "setuptools" in dependencies
    assert "wheel" in dependencies


def test_conflicting_duplicate_trivy_findings_fail():
    with pytest.raises(security_evidence.EvidenceError, match="conflicting"):
        _evaluate([_vulnerability(), _vulnerability(FixedVersion="19.0.2")])


def test_expired_stale_baseline_still_fails():
    result = _evaluate([], [_entry(expires_on="2026-10-02")])
    assert result["status"] == "FAIL"
    assert result["stale_baseline_requires_review"]


def _sbom():
    return {
        "spdxVersion": "SPDX-2.3",
        "SPDXID": "SPDXRef-DOCUMENT",
        "dataLicense": "CC0-1.0",
        "documentNamespace": "https://example.test/sbom",
        "creationInfo": {"creators": ["Tool: syft-1.52.0"]},
        "packages": [
            {"name": "mlflow", "SPDXID": "SPDXRef-Package-mlflow"},
            {
                "name": "libc6",
                "versionInfo": "2.41-12",
                "SPDXID": "SPDXRef-Package-libc6",
                "externalRefs": [
                    {"referenceType": "purl", "referenceLocator": "pkg:deb/debian/libc6@2.41-12"}
                ],
            },
        ],
    }


@pytest.mark.parametrize("payload", [{}, [None], [{}]])
def test_secret_scan_rejects_malformed_or_inconsistent_report(tmp_path, monkeypatch, payload):
    def fake_run(command, *, cwd=None):
        command = list(command)
        if command == ["gitleaks", "version"]:
            return _completed(command, stdout="8.30.1")
        Path(command[command.index("--report-path") + 1]).write_text(json.dumps(payload))
        return _completed(command)

    monkeypatch.setattr(security_evidence, "_run_command", fake_run)
    assert security_evidence.run_secret_scan(tmp_path, tmp_path / "evidence", "revision") == 2
    summary = json.loads((tmp_path / "evidence/gitleaks-summary.json").read_text())
    assert summary["status"] == "ERROR"


@pytest.mark.parametrize("failure", ["empty_inventory", "missing_version"])
def test_os_coverage_rejects_incomplete_sbom_inventory(failure):
    sbom = _sbom()
    if failure == "empty_inventory":
        sbom["packages"] = sbom["packages"][:1]
    else:
        del sbom["packages"][1]["versionInfo"]
    with pytest.raises(security_evidence.EvidenceError):
        security_evidence.validate_os_coverage(_report([]), sbom)


def test_os_coverage_accepts_wolfi_with_exact_package_identity():
    report = _report([])
    report["Metadata"]["OS"] = {"Family": "wolfi", "Name": "20230201"}
    sbom = _sbom()
    sbom["packages"][1]["externalRefs"][0]["referenceLocator"] = "pkg:apk/wolfi/libc6@2.41-12"
    assert security_evidence.validate_os_coverage(report, sbom)["status"] == "PASS"


@pytest.mark.parametrize(
    "package,expected",
    [
        (
            {"Name": "libc6", "Version": "2.41", "Release": "12+deb13u4"},
            ("libc6", "2.41-12+deb13u4"),
        ),
        (
            {"Name": "bsdutils", "Version": "2.41.5", "Release": "0+deb13u1", "Epoch": 1},
            ("bsdutils", "1:2.41.5-0+deb13u1"),
        ),
        ({"Name": "glibc", "Version": "2.43-r13"}, ("glibc", "2.43-r13")),
    ],
)
def test_os_coverage_preserves_distro_release_and_epoch(package, expected):
    assert security_evidence._os_package_identity(package) == expected
