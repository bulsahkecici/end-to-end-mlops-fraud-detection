#!/usr/bin/env python
"""Run an isolated production-like Compose deployment/rollback lifecycle.

The script uses disposable Compose volumes, random non-production credentials,
free host ports, and a temporary deployment-state directory. It always tears
the stack down, including volumes, before exiting.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class E2EFailure(RuntimeError):
    """A required production-like validation step failed."""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(
    command: list[str],
    *,
    env: dict[str, str],
    timeout: int = 600,
    show_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    print(f"-> {' '.join(command)}", flush=True)
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if show_output and result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        detail = result.stderr.strip() or "command failed without stderr"
        raise E2EFailure(f"command exited {result.returncode}: {detail[-3000:]}")
    return result


def _http_json(url: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url)
    if payload is not None:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _wait_json(url: str, *, timeout: int = 120) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status, body = _http_json(url)
            if status == 200:
                return body
            last_error = E2EFailure(f"status={status} body={body}")
        except Exception as exc:  # noqa: BLE001 - retry startup races
            last_error = exc
        time.sleep(2)
    raise E2EFailure(f"timed out waiting for {url}: {last_error}")


def _wait_http_ok(url: str, *, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - retry startup races
            last_error = exc
        time.sleep(2)
    raise E2EFailure(f"timed out waiting for HTTP 200 from {url}: {last_error}")


def _assert_serving(base_url: str, expected_version: str) -> None:
    ready = _wait_json(f"{base_url}/ready")
    if ready.get("model_version") != expected_version:
        raise E2EFailure(
            f"served version {ready.get('model_version')!r} != expected {expected_version!r}"
        )
    status, body = _http_json(f"{base_url}/predict", {"records": [{"TransactionAmt": 125.0}]})
    if status != 200 or len(body.get("predictions", [])) != 1:
        raise E2EFailure(f"prediction failed: status={status} body={body}")


def _assert_nginx_rate_limit(base_url: str) -> None:
    codes: list[int] = []
    data = json.dumps({"records": [{"TransactionAmt": 1.0}]}).encode()
    for _ in range(50):
        request = urllib.request.Request(
            f"{base_url}/predict",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                codes.append(response.status)
        except urllib.error.HTTPError as exc:
            codes.append(exc.code)
    if 200 not in codes or 429 not in codes:
        raise E2EFailure(f"NGINX rate-limit smoke expected 200 and 429, got {sorted(set(codes))}")


def main() -> int:
    project_name = f"phase4e2e-{secrets.token_hex(4)}"
    compose = [
        "docker",
        "compose",
        "-p",
        project_name,
        "--profile",
        "production-like",
    ]
    mlflow_port, api_port, nginx_port, minio_port, minio_console_port = (
        _free_port() for _ in range(5)
    )

    with tempfile.TemporaryDirectory(prefix="phase4_deployment_") as temporary:
        state_dir = Path(temporary) / "state"
        state_dir.mkdir(mode=0o755)
        state_path = state_dir / "current.json"
        env = os.environ.copy()
        env.update(
            {
                "POSTGRES_DB": "mlflow",
                "POSTGRES_USER": "mlflow",
                "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
                "MINIO_ROOT_USER": "phase4e2e",
                "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(24),
                "MLFLOW_ARTIFACT_BUCKET": "mlflow-artifacts",
                "MLFLOW_HOST_PORT": str(mlflow_port),
                "API_HOST_PORT": str(api_port),
                "NGINX_HOST_PORT": str(nginx_port),
                "MINIO_HOST_PORT": str(minio_port),
                "MINIO_CONSOLE_HOST_PORT": str(minio_console_port),
                "DEPLOYMENT_STATE_DIR": str(state_dir),
                "DEPLOYMENT_STATE_PATH": str(state_path),
                "MLFLOW_TRACKING_URI": f"http://127.0.0.1:{mlflow_port}",
                "MIN_PR_AUC": "0",
                "MIN_RECALL": "0",
                "MAX_CHAMPION_REGRESSION": "1",
            }
        )

        passed = False
        try:
            _run(compose + ["config"], env=env)
            _run(compose + ["build", "mlflow-prod", "api-prod"], env=env, timeout=1200)
            _run(
                compose + ["up", "-d", "postgres", "minio", "minio-init", "mlflow-prod"],
                env=env,
                timeout=600,
            )
            _run(
                compose
                + [
                    "exec",
                    "-T",
                    "postgres",
                    "pg_isready",
                    "-U",
                    env["POSTGRES_USER"],
                    "-d",
                    env["POSTGRES_DB"],
                ],
                env=env,
            )
            _wait_http_ok(f"http://127.0.0.1:{minio_port}/minio/health/live")
            _run(compose + ["run", "--rm", "--no-deps", "minio-init"], env=env)
            _wait_http_ok(f"http://127.0.0.1:{mlflow_port}/health")

            from src.config import settings
            from src.deployment.lifecycle import deploy_champion, rollback_previous
            from src.modeling.train import run_training
            from src.registry.promote import run_promotion_checks

            settings.mlflow_tracking_uri = env["MLFLOW_TRACKING_URI"]
            settings.deployment_state_path = state_path
            settings.min_pr_auc = 0.0
            settings.min_recall = 0.0
            settings.max_champion_regression = 1.0

            print("-> train/register/promote/deploy synthetic version 1", flush=True)
            first = run_training(
                data_source="synthetic",
                n_synthetic=2000,
                seed=404,
                tracking_uri=settings.mlflow_tracking_uri,
            )
            first_promotion = run_promotion_checks(tracking_uri=settings.mlflow_tracking_uri)
            if not first_promotion["promoted"]:
                raise E2EFailure(f"first promotion failed: {first_promotion['checks']}")
            first_deployment = deploy_champion()
            if first_deployment.model_version != first["model_version"]:
                raise E2EFailure("first deployment did not freeze the promoted version")

            _run(compose + ["up", "-d", "--force-recreate", "api-prod"], env=env)
            _assert_serving(f"http://127.0.0.1:{api_port}", first["model_version"])
            _run(compose + ["up", "-d", "nginx-prod"], env=env)
            _assert_serving(f"http://127.0.0.1:{nginx_port}", first["model_version"])

            print("-> promote and deploy synthetic version 2", flush=True)
            second = run_training(
                data_source="synthetic",
                n_synthetic=2000,
                seed=404,
                tracking_uri=settings.mlflow_tracking_uri,
                lgbm_overrides={"num_leaves": 7, "learning_rate": 0.02},
            )
            second_promotion = run_promotion_checks(tracking_uri=settings.mlflow_tracking_uri)
            if not second_promotion["promoted"]:
                raise E2EFailure(f"second promotion failed: {second_promotion['checks']}")
            second_deployment = deploy_champion()
            if second_deployment.previous_deployment_id != first_deployment.deployment_id:
                raise E2EFailure("second deployment did not retain previous deployment identity")

            # Explicit state change alone must not hot-swap the running process.
            _assert_serving(f"http://127.0.0.1:{api_port}", first["model_version"])
            _run(compose + ["up", "-d", "--force-recreate", "api-prod"], env=env)
            _assert_serving(f"http://127.0.0.1:{nginx_port}", second["model_version"])

            print("-> roll back to the previous immutable deployment", flush=True)
            rollback = rollback_previous()
            if rollback.model_version != first["model_version"]:
                raise E2EFailure("rollback did not restore the previous immutable version")
            _assert_serving(f"http://127.0.0.1:{api_port}", second["model_version"])
            _run(compose + ["up", "-d", "--force-recreate", "api-prod"], env=env)
            _assert_serving(f"http://127.0.0.1:{nginx_port}", first["model_version"])
            _assert_nginx_rate_limit(f"http://127.0.0.1:{nginx_port}")

            print(
                "PASS production-like lifecycle "
                f"run={first['run_id']} first={first['model_version']} "
                f"second={second['model_version']} rollback={rollback.model_version}",
                flush=True,
            )
            passed = True
        except Exception as exc:  # noqa: BLE001 - preserve cleanup for every failure
            print(f"FAIL production-like lifecycle: {exc}", file=sys.stderr, flush=True)
            try:
                _run(compose + ["ps", "--all"], env=env, show_output=True)
                _run(
                    compose + ["logs", "--no-color", "--tail", "100"],
                    env=env,
                    show_output=True,
                )
            except Exception as diagnostic_error:  # noqa: BLE001
                print(f"diagnostics failed: {diagnostic_error}", file=sys.stderr)
        finally:
            try:
                _run(
                    compose + ["down", "-v", "--remove-orphans"],
                    env=env,
                    timeout=300,
                )
            except Exception as cleanup_error:  # noqa: BLE001
                print(f"cleanup failed: {cleanup_error}", file=sys.stderr)
                passed = False
        return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
