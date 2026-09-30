#!/usr/bin/env python
"""Single-command local end-to-end validation.

Trains a model on synthetic data, registers it as `candidate`, runs the
promotion gate, explicitly deploys the approved immutable version, starts the
FastAPI service against that deployment state, and exercises /health, /ready
and /predict. Prints a clear
step-by-step PASS/FAIL report and exits non-zero on any failure.

Uses an isolated, throwaway sqlite MLflow store by default (does not touch
a locally running MLflow server) unless MLFLOW_TRACKING_URI is already set
in the environment, so it's safe to run repeatedly and safe to use in CI.

Usage:
    python scripts/validate_e2e.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

API_PORT = 8321
API_BASE = f"http://127.0.0.1:{API_PORT}"

results: list[tuple[str, bool, str]] = []


def step(name: str):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            print(f"-> {name} ...", flush=True)
            try:
                detail = fn(*args, **kwargs) or ""
                results.append((name, True, str(detail)))
                print(f"   OK  {detail}", flush=True)
                return True
            except Exception as exc:  # noqa: BLE001 - report every failure, don't crash mid-report
                results.append((name, False, str(exc)))
                print(f"   FAIL  {exc}", flush=True)
                return False

        return wrapper

    return decorator


def _http_get(path: str, timeout: float = 5.0):
    with urllib.request.urlopen(f"{API_BASE}{path}", timeout=timeout) as resp:
        return resp.status, json.loads(resp.read())


def _http_post_json(path: str, payload: dict, timeout: float = 5.0):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@step("1. mlflow tracking store reachable/creatable")
def check_mlflow_store() -> str:
    import mlflow

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    client = mlflow.MlflowClient()
    client.search_experiments(max_results=1)
    return os.environ["MLFLOW_TRACKING_URI"]


@step("2. train a model on synthetic data")
def train_model() -> str:
    from src.modeling.train import run_training

    result = run_training(data_source="synthetic", n_synthetic=6000, seed=42, register=True)
    if not result.get("model_version"):
        raise RuntimeError("training did not produce a registered model version")
    global _train_result
    _train_result = result
    return f"version={result['model_version']} val_pr_auc={result['val_metrics']['pr_auc']:.3f}"


@step("3. candidate alias assigned")
def check_candidate_alias() -> str:
    import mlflow

    from src.config import settings

    client = mlflow.MlflowClient()
    mv = client.get_model_version_by_alias(settings.model_name, settings.candidate_alias)
    return f"{settings.candidate_alias} -> version {mv.version}"


@step("4. promotion checks (candidate -> champion)")
def run_promotion() -> str:
    from src.registry.promote import run_promotion_checks

    result = run_promotion_checks()
    if not result["promoted"]:
        failed = [k for k, v in result["checks"].items() if not v["passed"]]
        raise RuntimeError(f"promotion blocked, failed checks: {failed}")
    return f"promoted version {result['candidate_version']}"


@step("5. deploy approved champion as an immutable serving target")
def deploy_model() -> str:
    from src.deployment.lifecycle import deploy_champion

    deployment = deploy_champion()
    global _deployment_state
    _deployment_state = deployment
    return f"deployment={deployment.deployment_id} version={deployment.model_version}"


@step("6. start API and wait for it to load the deployed model")
def start_api() -> str:
    global _api_process
    env = os.environ.copy()
    _api_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(API_PORT),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            status, _ = _http_get("/health", timeout=2)
            if status == 200:
                return f"pid={_api_process.pid}"
        except Exception:
            pass
        if _api_process.poll() is not None:
            output = (
                _api_process.stdout.read().decode(errors="replace") if _api_process.stdout else ""
            )
            raise RuntimeError(f"API process exited early:\n{output[-2000:]}")
        time.sleep(0.5)
    raise RuntimeError("API did not become healthy within 30s")


@step("7. GET /ready reports the immutable deployed model")
def check_ready() -> str:
    status, body = _http_get("/ready")
    if (
        status != 200
        or body.get("model_version") != _deployment_state.model_version
        or body.get("run_id") != _deployment_state.run_id
    ):
        raise RuntimeError(f"status={status} body={body}")
    return f"model_version={body['model_version']}"


@step("8. POST /predict (README-style single field) returns a valid probability")
def check_predict() -> str:
    status, body = _http_post_json("/predict", {"records": [{"TransactionAmt": 100.0}]})
    if status != 200:
        raise RuntimeError(f"status={status} body={body}")
    pred = body["predictions"][0]
    if not (0.0 <= pred["fraud_probability"] <= 1.0):
        raise RuntimeError(f"fraud_probability out of range: {pred}")
    return f"fraud_probability={pred['fraud_probability']:.4f}"


@step("9. POST /predict (multi-record batch) returns matching count")
def check_predict_batch() -> str:
    records = [{"TransactionAmt": 1.0}, {"TransactionAmt": 500.0, "ProductCD": "C"}]
    status, body = _http_post_json("/predict", {"records": records})
    if status != 200 or len(body["predictions"]) != len(records):
        raise RuntimeError(f"status={status} body={body}")
    return f"{len(body['predictions'])} predictions"


def main() -> int:
    if "MLFLOW_TRACKING_URI" not in os.environ:
        tmp_dir = tempfile.mkdtemp(prefix="validate_e2e_mlflow_")
        os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_dir}/mlflow.db"
    if "DEPLOYMENT_STATE_PATH" not in os.environ:
        state_dir = tempfile.mkdtemp(prefix="validate_e2e_deployment_")
        os.environ["DEPLOYMENT_STATE_PATH"] = str(Path(state_dir) / "current.json")
    print(f"Using MLFLOW_TRACKING_URI={os.environ['MLFLOW_TRACKING_URI']}\n")

    global _api_process
    _api_process = None

    ok = True
    ok &= check_mlflow_store()
    ok &= train_model()
    ok &= check_candidate_alias()
    ok &= run_promotion()
    ok &= deploy_model()
    if ok:
        ok &= start_api()
    if _api_process is not None and _api_process.poll() is None:
        ok &= check_ready()
        ok &= check_predict()
        ok &= check_predict_batch()

    if _api_process is not None and _api_process.poll() is None:
        _api_process.terminate()
        try:
            _api_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _api_process.kill()

    print("\n=== validate_e2e summary ===")
    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if not passed else ""))

    n_failed = sum(1 for _, passed, _ in results if not passed)
    print(f"\n{len(results) - n_failed}/{len(results)} steps passed.")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
