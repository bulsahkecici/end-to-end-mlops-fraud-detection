"""Immutable deployed-model loading and lifecycle state for FastAPI.

The service reads an explicit deployment manifest at startup and loads only
the immutable MLflow model version recorded there. Registry alias movement
cannot change a running process or the model selected at its next restart.
"""

from __future__ import annotations

import logging

import mlflow
import mlflow.pyfunc
from fastapi import HTTPException

from src.api.validation import ModelFeatureContract
from src.config import settings
from src.deployment.lifecycle import DeploymentState, load_deployment_state

logger = logging.getLogger("src.api")


class ModelLoadError(RuntimeError):
    """Raised when no servable model version can be resolved from the registry."""


class ModelState:
    """Mutable holder for the currently loaded model, set once at startup."""

    def __init__(self) -> None:
        self.model: object | None = None
        self.model_version: str | None = None
        self.model_source: str | None = None
        self.run_id: str | None = None
        self.deployed_at: str | None = None
        self.load_error: str | None = None
        self.feature_contract: ModelFeatureContract | None = None

    @property
    def is_ready(self) -> bool:
        return self.model is not None


model_state = ModelState()


def resolve_and_load_model() -> tuple[object, DeploymentState]:
    """Load the exact immutable version in deployment state."""
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    client = mlflow.MlflowClient()
    try:
        deployment = load_deployment_state(settings.deployment_state_path)
    except Exception as exc:
        raise ModelLoadError(f"Invalid deployment state: {exc}") from exc
    if deployment.model_name != settings.model_name:
        raise ModelLoadError(
            f"Deployment state model {deployment.model_name!r} does not match configured "
            f"model {settings.model_name!r}"
        )
    try:
        registry_version = client.get_model_version(deployment.model_name, deployment.model_version)
    except Exception as exc:
        raise ModelLoadError(
            f"Could not resolve deployed immutable model {deployment.model_name!r} "
            f"version {deployment.model_version}: {exc}"
        ) from exc
    if str(registry_version.run_id) != deployment.run_id:
        raise ModelLoadError("Deployed model run ID does not match the registry model version")

    uri = f"models:/{deployment.model_name}/{deployment.model_version}"
    try:
        pyfunc_model = mlflow.pyfunc.load_model(uri)
    except Exception as exc:
        raise ModelLoadError(f"Could not load deployed immutable model {uri}: {exc}") from exc
    # Unwrap to the raw FraudModelWrapper and call it directly. mlflow's
    # PyFuncModel.predict() enforces the logged input *schema* strictly
    # (rejects int64-vs-float64 mismatches, etc) before our code ever runs
    # — which defeats the whole point of the ColumnAligner step tolerating
    # missing/extra/reordered/loosely-typed columns. The signature is still
    # logged (for the MLflow UI / other tooling) but is not used to gate
    # requests here.
    model = pyfunc_model.unwrap_python_model()
    return model, deployment


def load_model_into_state() -> None:
    from src.api.metrics import MODEL_INFO, MODEL_LOAD_FAILURES_TOTAL, MODEL_LOADED

    try:
        model, deployment = resolve_and_load_model()
        feature_contract = ModelFeatureContract.from_model(model)
        model_state.model = model
        model_state.model_version = deployment.model_version
        model_state.model_source = f"version:{deployment.model_version}"
        model_state.run_id = deployment.run_id
        model_state.deployed_at = deployment.deployed_at
        model_state.load_error = None
        model_state.feature_contract = feature_contract
        MODEL_LOADED.set(1)
        MODEL_INFO.labels(
            model_name=deployment.model_name, model_version=deployment.model_version
        ).set(1)
        logger.info(
            "model_loaded",
            extra={
                "model_name": deployment.model_name,
                "model_version": deployment.model_version,
                "deployment_id": deployment.deployment_id,
                "action": deployment.action,
            },
        )
    except Exception as exc:  # noqa: BLE001 - intentionally broad: startup must never crash the app
        model_state.model = None
        model_state.model_version = None
        model_state.model_source = None
        model_state.run_id = None
        model_state.deployed_at = None
        model_state.load_error = str(exc)
        model_state.feature_contract = None
        MODEL_LOADED.set(0)
        MODEL_LOAD_FAILURES_TOTAL.inc()
        logger.warning("model_load_failed", extra={"error_type": type(exc).__name__})


def require_model() -> object:
    """FastAPI dependency: return the loaded model or raise a clean 503."""
    if not model_state.is_ready:
        raise HTTPException(
            status_code=503,
            detail="Model not loaded. Deploy an approved model, then restart the service.",
        )
    return model_state.model
