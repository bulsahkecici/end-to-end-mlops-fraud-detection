"""Deploy and roll back approved immutable MLflow model versions.

Promotion changes the registry's ``champion`` alias. Deployment resolves that
alias once and persists the resulting immutable version in an atomic manifest.
The API reads only that manifest; it never follows the alias itself.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mlflow

from src.config import settings
from src.registry.compare import RegistryLookupError, get_alias_model_version

SCHEMA_VERSION = 1
_DEPLOYMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class DeploymentStateError(RuntimeError):
    """Deployment state or a requested lifecycle transition is invalid."""


@dataclass(frozen=True)
class DeploymentState:
    """Auditable snapshot of one explicit serving-state transition."""

    schema_version: int
    deployment_id: str
    action: str
    model_name: str
    model_version: str
    run_id: str
    source_alias: str
    deployed_at: str
    previous_deployment_id: str | None
    previous_model_version: str | None
    rollback_of_deployment_id: str | None
    git_commit: str | None
    git_branch: str | None
    git_dirty: bool | None

    @classmethod
    def from_dict(cls, payload: Any) -> DeploymentState:
        """Parse and strictly validate a deployment-state payload."""
        if not isinstance(payload, dict):
            raise DeploymentStateError("deployment state must be a JSON object")
        required = {field.name for field in cls.__dataclass_fields__.values()}
        missing = sorted(required - payload.keys())
        extra = sorted(payload.keys() - required)
        if missing or extra:
            details = []
            if missing:
                details.append(f"missing fields: {', '.join(missing)}")
            if extra:
                details.append(f"unexpected fields: {', '.join(extra)}")
            raise DeploymentStateError("invalid deployment state: " + "; ".join(details))
        try:
            state = cls(**payload)
        except TypeError as exc:
            raise DeploymentStateError(f"invalid deployment state fields: {exc}") from exc
        state.validate()
        return state

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise DeploymentStateError(
                f"unsupported deployment state schema_version={self.schema_version!r}"
            )
        if not isinstance(self.deployment_id, str) or not _DEPLOYMENT_ID_PATTERN.fullmatch(
            self.deployment_id
        ):
            raise DeploymentStateError("deployment_id is missing or invalid")
        if self.action not in {"deploy", "rollback"}:
            raise DeploymentStateError("action must be 'deploy' or 'rollback'")
        for name in ("model_name", "model_version", "run_id", "source_alias", "deployed_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DeploymentStateError(f"{name} must be a non-empty string")
        if not self.model_version.isdigit() or int(self.model_version) < 1:
            raise DeploymentStateError("model_version must be a positive integer string")
        try:
            parsed_time = datetime.fromisoformat(self.deployed_at)
        except ValueError as exc:
            raise DeploymentStateError("deployed_at must be an ISO-8601 timestamp") from exc
        if parsed_time.tzinfo is None:
            raise DeploymentStateError("deployed_at must include a timezone")
        for name in (
            "previous_deployment_id",
            "previous_model_version",
            "rollback_of_deployment_id",
            "git_commit",
            "git_branch",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise DeploymentStateError(f"{name} must be null or a non-empty string")
        if self.git_dirty is not None and not isinstance(self.git_dirty, bool):
            raise DeploymentStateError("git_dirty must be a boolean or null")
        if self.action == "rollback" and self.rollback_of_deployment_id is None:
            raise DeploymentStateError("rollback state must identify the deployment it rolled back")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_deployment_state(path: Path | str | None = None) -> DeploymentState:
    """Load and validate the current deployment state."""
    state_path = Path(path or settings.deployment_state_path)
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DeploymentStateError(f"deployment state does not exist: {state_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentStateError(f"could not read deployment state {state_path}: {exc}") from exc
    return DeploymentState.from_dict(payload)


def _load_history_state(state_path: Path, deployment_id: str) -> DeploymentState:
    if not _DEPLOYMENT_ID_PATTERN.fullmatch(deployment_id):
        raise DeploymentStateError("previous deployment ID is invalid")
    history_path = state_path.parent / "history" / f"{deployment_id}.json"
    state = load_deployment_state(history_path)
    if state.deployment_id != deployment_id:
        raise DeploymentStateError("deployment history identity does not match its filename")
    return state


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON through a same-directory temporary file and atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
        temporary = None
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Some filesystems do not support directory fsync. The file-level
            # fsync and atomic replace still prevent partial JSON manifests.
            pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    """Atomically create an append-only JSON record without replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        # A hard link publishes the fully written inode and fails with
        # FileExistsError if the history identity already exists. Unlike
        # os.replace(), it can never overwrite an audit event.
        os.link(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _state_lock(state_path: Path) -> Iterator[None]:
    """Serialize local writers so the previous-deployment chain stays coherent."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.parent / ".deployment.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - Windows fallback
            pass
        try:
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except ImportError:  # pragma: no cover - Windows fallback
                pass


def _git_value(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=settings.project_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value or None


def _git_context() -> tuple[str | None, str | None, bool | None]:
    commit = _git_value("rev-parse", "HEAD")
    branch = _git_value("branch", "--show-current")
    status = _git_value("status", "--porcelain")
    dirty = None if commit is None else bool(status)
    return commit, branch, dirty


def _new_deployment_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{uuid.uuid4().hex}"


def _verify_loadable_model(
    client: Any,
    model_name: str,
    model_version: str,
    expected_run_id: str,
    model_loader: Callable[[str], Any],
) -> None:
    try:
        registry_version = client.get_model_version(model_name, model_version)
    except Exception as exc:
        raise DeploymentStateError(
            f"could not resolve immutable model {model_name!r} version {model_version}: {exc}"
        ) from exc
    actual_run_id = str(registry_version.run_id)
    if actual_run_id != expected_run_id:
        raise DeploymentStateError(
            "registry run ID does not match the recorded deployment target "
            f"({actual_run_id!r} != {expected_run_id!r})"
        )
    uri = f"models:/{model_name}/{model_version}"
    try:
        model_loader(uri)
    except Exception as exc:
        raise DeploymentStateError(f"could not load immutable model {uri}: {exc}") from exc


def _persist_transition(state_path: Path, state: DeploymentState) -> None:
    state.validate()
    history_path = state_path.parent / "history" / f"{state.deployment_id}.json"
    if history_path.exists():
        raise DeploymentStateError(f"deployment history already exists: {state.deployment_id}")
    try:
        _write_json_exclusive(history_path, state.as_dict())
        _write_json_atomic(state_path, state.as_dict())
    except OSError as exc:
        raise DeploymentStateError(f"could not persist deployment state: {exc}") from exc


def deploy_champion(
    *,
    state_path: Path | str | None = None,
    model_name: str | None = None,
    tracking_uri: str | None = None,
    expected_version: str | None = None,
    client: Any | None = None,
    model_loader: Callable[[str], Any] | None = None,
) -> DeploymentState:
    """Freeze the current approved champion into explicit deployment state."""
    target_path = Path(state_path or settings.deployment_state_path)
    target_model = model_name or settings.model_name
    mlflow.set_tracking_uri(tracking_uri or settings.mlflow_tracking_uri)
    registry = client or mlflow.MlflowClient()
    loader = model_loader or mlflow.pyfunc.load_model

    try:
        champion = get_alias_model_version(registry, target_model, settings.champion_alias)
    except RegistryLookupError as exc:
        raise DeploymentStateError(str(exc)) from exc
    if champion is None:
        raise DeploymentStateError(
            f"approved alias {settings.champion_alias!r} does not exist for {target_model!r}"
        )
    version = str(champion.version)
    run_id = str(champion.run_id)
    if expected_version is not None and str(expected_version) != version:
        raise DeploymentStateError(
            f"requested version {expected_version!r} is not the currently approved "
            f"{settings.champion_alias!r} version {version!r}"
        )

    _verify_loadable_model(registry, target_model, version, run_id, loader)
    with _state_lock(target_path):
        current: DeploymentState | None
        try:
            current = load_deployment_state(target_path)
        except DeploymentStateError:
            if target_path.exists():
                raise
            current = None
        if (
            current is not None
            and current.model_name == target_model
            and current.model_version == version
            and current.run_id == run_id
        ):
            return current

        commit, branch, dirty = _git_context()
        state = DeploymentState(
            schema_version=SCHEMA_VERSION,
            deployment_id=_new_deployment_id(),
            action="deploy",
            model_name=target_model,
            model_version=version,
            run_id=run_id,
            source_alias=settings.champion_alias,
            deployed_at=datetime.now(UTC).isoformat(),
            previous_deployment_id=current.deployment_id if current else None,
            previous_model_version=current.model_version if current else None,
            rollback_of_deployment_id=None,
            git_commit=commit,
            git_branch=branch,
            git_dirty=dirty,
        )
        # Re-resolve immediately beside persistence. This closes the material
        # validate-then-write race without making the API follow the alias.
        try:
            stable_champion = get_alias_model_version(
                registry, target_model, settings.champion_alias
            )
        except RegistryLookupError as exc:
            raise DeploymentStateError(str(exc)) from exc
        if (
            stable_champion is None
            or str(stable_champion.version) != version
            or str(stable_champion.run_id) != run_id
        ):
            raise DeploymentStateError("champion alias moved or disappeared during deployment")
        _persist_transition(target_path, state)
        return state


def rollback_previous(
    *,
    state_path: Path | str | None = None,
    tracking_uri: str | None = None,
    client: Any | None = None,
    model_loader: Callable[[str], Any] | None = None,
) -> DeploymentState:
    """Restore the immediately previous known-good deployment target."""
    target_path = Path(state_path or settings.deployment_state_path)
    mlflow.set_tracking_uri(tracking_uri or settings.mlflow_tracking_uri)
    registry = client or mlflow.MlflowClient()
    loader = model_loader or mlflow.pyfunc.load_model

    with _state_lock(target_path):
        current = load_deployment_state(target_path)
        if current.action == "rollback":
            return current
        if current.previous_deployment_id is None:
            raise DeploymentStateError("current deployment has no previous deployment to restore")
        previous = _load_history_state(target_path, current.previous_deployment_id)
        _verify_loadable_model(
            registry,
            previous.model_name,
            previous.model_version,
            previous.run_id,
            loader,
        )

        commit, branch, dirty = _git_context()
        state = DeploymentState(
            schema_version=SCHEMA_VERSION,
            deployment_id=_new_deployment_id(),
            action="rollback",
            model_name=previous.model_name,
            model_version=previous.model_version,
            run_id=previous.run_id,
            source_alias="deployment-history",
            deployed_at=datetime.now(UTC).isoformat(),
            previous_deployment_id=current.deployment_id,
            previous_model_version=current.model_version,
            rollback_of_deployment_id=current.deployment_id,
            git_commit=commit,
            git_branch=branch,
            git_dirty=dirty,
        )
        _persist_transition(target_path, state)
        return state


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-path",
        type=Path,
        default=settings.deployment_state_path,
        help="current deployment-state JSON path",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    deploy_parser = subparsers.add_parser("deploy", help="deploy the approved champion")
    deploy_parser.add_argument(
        "--expected-version",
        help="fail unless this version is the current champion; never bypasses approval",
    )
    subparsers.add_parser("inspect", help="print the current deployment state")
    subparsers.add_parser("rollback", help="restore the previous recorded deployment")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "deploy":
            state = deploy_champion(
                state_path=args.state_path,
                expected_version=args.expected_version,
            )
        elif args.command == "rollback":
            state = rollback_previous(state_path=args.state_path)
        else:
            state = load_deployment_state(args.state_path)
    except DeploymentStateError as exc:
        print(f"deployment error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(state.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
