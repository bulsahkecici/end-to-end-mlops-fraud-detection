from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.deployment.lifecycle import (
    DeploymentState,
    DeploymentStateError,
    deploy_champion,
    load_deployment_state,
    rollback_previous,
)


class FakeRegistry:
    def __init__(self, version: str = "1", run_id: str = "run-1") -> None:
        self.champion = SimpleNamespace(version=version, run_id=run_id)
        self.versions = {version: run_id}
        self.alias_error: Exception | None = None

    def get_model_version_by_alias(self, _model_name: str, _alias: str):
        if self.alias_error is not None:
            raise self.alias_error
        return self.champion

    def get_model_version(self, _model_name: str, version: str):
        if version not in self.versions:
            raise RuntimeError("registry version unavailable")
        return SimpleNamespace(version=version, run_id=self.versions[version])


@pytest.fixture(autouse=True)
def stable_git_context(monkeypatch):
    monkeypatch.setattr(
        "src.deployment.lifecycle._git_context",
        lambda: ("abc123", "hardening/phase-4-deployment", True),
    )


def _deploy(path: Path, registry: FakeRegistry, expected_version: str | None = None):
    return deploy_champion(
        state_path=path,
        client=registry,
        model_loader=lambda _uri: object(),
        expected_version=expected_version,
    )


def test_deploy_freezes_champion_and_records_provenance(tmp_path):
    state_path = tmp_path / "current.json"
    registry = FakeRegistry()

    state = _deploy(state_path, registry)

    assert state.model_version == "1"
    assert state.run_id == "run-1"
    assert state.source_alias == "champion"
    assert state.git_commit == "abc123"
    assert state.git_branch == "hardening/phase-4-deployment"
    assert state.git_dirty is True
    assert load_deployment_state(state_path) == state
    assert (tmp_path / "history" / f"{state.deployment_id}.json").exists()


def test_normal_deploy_rejects_unapproved_version(tmp_path):
    with pytest.raises(DeploymentStateError, match="not the currently approved"):
        _deploy(tmp_path / "current.json", FakeRegistry(), expected_version="2")


def test_deploy_fails_when_champion_is_missing(tmp_path):
    registry = FakeRegistry()
    registry.champion = None
    with pytest.raises(DeploymentStateError, match="does not exist"):
        _deploy(tmp_path / "current.json", registry)


def test_deploy_fails_closed_on_registry_error(tmp_path):
    registry = FakeRegistry()
    registry.alias_error = RuntimeError("registry offline")
    with pytest.raises(DeploymentStateError, match="registry offline"):
        _deploy(tmp_path / "current.json", registry)


def test_failed_model_validation_preserves_current_deployment(tmp_path):
    state_path = tmp_path / "current.json"
    registry = FakeRegistry()
    current = _deploy(state_path, registry)
    registry.champion = SimpleNamespace(version="2", run_id="run-2")
    registry.versions["2"] = "run-2"

    with pytest.raises(DeploymentStateError, match="could not load immutable"):
        deploy_champion(
            state_path=state_path,
            client=registry,
            model_loader=lambda _uri: (_ for _ in ()).throw(OSError("artifact unavailable")),
        )
    assert load_deployment_state(state_path) == current


def test_deploy_fails_if_champion_moves_during_validation(tmp_path):
    state_path = tmp_path / "current.json"
    registry = FakeRegistry()
    original = _deploy(state_path, registry)
    registry.versions["2"] = "run-2"
    lookups = 0

    def moving_alias(_model_name, _alias):
        nonlocal lookups
        lookups += 1
        if lookups == 1:
            return SimpleNamespace(version="2", run_id="run-2")
        return SimpleNamespace(version="3", run_id="run-3")

    registry.get_model_version_by_alias = moving_alias
    with pytest.raises(DeploymentStateError, match="alias moved"):
        _deploy(state_path, registry)
    assert load_deployment_state(state_path) == original


def test_second_deploy_records_previous_and_redeploy_is_idempotent(tmp_path):
    state_path = tmp_path / "current.json"
    registry = FakeRegistry()
    first = _deploy(state_path, registry)
    assert _deploy(state_path, registry).deployment_id == first.deployment_id

    registry.champion = SimpleNamespace(version="2", run_id="run-2")
    registry.versions["2"] = "run-2"
    second = _deploy(state_path, registry)

    assert second.model_version == "2"
    assert second.previous_deployment_id == first.deployment_id
    assert second.previous_model_version == "1"
    assert len(list((tmp_path / "history").glob("*.json"))) == 2


def test_rollback_restores_previous_version_and_is_idempotent(tmp_path):
    state_path = tmp_path / "current.json"
    registry = FakeRegistry()
    first = _deploy(state_path, registry)
    registry.champion = SimpleNamespace(version="2", run_id="run-2")
    registry.versions["2"] = "run-2"
    second = _deploy(state_path, registry)

    rolled_back = rollback_previous(
        state_path=state_path,
        client=registry,
        model_loader=lambda _uri: object(),
    )

    assert rolled_back.action == "rollback"
    assert rolled_back.model_version == first.model_version
    assert rolled_back.rollback_of_deployment_id == second.deployment_id
    assert registry.champion.version == "2"
    assert (
        rollback_previous(
            state_path=state_path,
            client=registry,
            model_loader=lambda _uri: object(),
        ).deployment_id
        == rolled_back.deployment_id
    )


def test_failed_rollback_preserves_current_state(tmp_path):
    state_path = tmp_path / "current.json"
    registry = FakeRegistry()
    _deploy(state_path, registry)
    registry.champion = SimpleNamespace(version="2", run_id="run-2")
    registry.versions["2"] = "run-2"
    current = _deploy(state_path, registry)
    del registry.versions["1"]

    with pytest.raises(DeploymentStateError, match="could not resolve immutable"):
        rollback_previous(
            state_path=state_path,
            client=registry,
            model_loader=lambda _uri: object(),
        )
    assert load_deployment_state(state_path) == current


def test_atomic_current_write_failure_preserves_valid_current(tmp_path, monkeypatch):
    state_path = tmp_path / "current.json"
    registry = FakeRegistry()
    first = _deploy(state_path, registry)
    registry.champion = SimpleNamespace(version="2", run_id="run-2")
    registry.versions["2"] = "run-2"

    real_replace = __import__("os").replace

    def fail_current_replace(source, destination):
        if Path(destination) == state_path:
            raise OSError("simulated atomic replace failure")
        real_replace(source, destination)

    monkeypatch.setattr("src.deployment.lifecycle.os.replace", fail_current_replace)
    with pytest.raises(DeploymentStateError, match="simulated atomic replace failure"):
        _deploy(state_path, registry)
    assert load_deployment_state(state_path) == first


def test_history_identity_collision_never_overwrites_existing_event(tmp_path, monkeypatch):
    state_path = tmp_path / "current.json"
    registry = FakeRegistry()
    first = _deploy(state_path, registry)
    history_path = tmp_path / "history" / f"{first.deployment_id}.json"
    original_history = history_path.read_bytes()
    registry.champion = SimpleNamespace(version="2", run_id="run-2")
    registry.versions["2"] = "run-2"
    monkeypatch.setattr("src.deployment.lifecycle._new_deployment_id", lambda: first.deployment_id)

    with pytest.raises(DeploymentStateError, match="history already exists"):
        _deploy(state_path, registry)
    assert history_path.read_bytes() == original_history
    assert load_deployment_state(state_path) == first


@pytest.mark.parametrize("contents", [None, "not-json", json.dumps({"schema_version": 1})])
def test_missing_or_corrupt_deployment_state_fails(tmp_path, contents):
    state_path = tmp_path / "current.json"
    if contents is not None:
        state_path.write_text(contents)
    with pytest.raises(DeploymentStateError):
        load_deployment_state(state_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [("model_name", ""), ("model_version", "champion"), ("run_id", "")],
)
def test_manifest_rejects_invalid_model_identity(tmp_path, field, value):
    registry = FakeRegistry()
    payload = _deploy(tmp_path / "current.json", registry).as_dict()
    payload[field] = value
    with pytest.raises(DeploymentStateError):
        DeploymentState.from_dict(payload)
