"""Model loading and lifecycle state for the FastAPI service.

The service loads the model exclusively from the MLflow Model Registry:
first the ``champion`` alias, falling back to the legacy ``Production``
stage for registries that still use the old stage-based API. It never reads
a local feature_meta.json — all preprocessing lives inside the logged
pipeline itself (see ``src/features/pipeline.py`` and
``src/modeling/mlflow_wrapper.py``).
"""

from __future__ import annotations

import logging

import mlflow
import mlflow.pyfunc
from fastapi import HTTPException

from src.config import settings

logger = logging.getLogger("src.api")


class ModelLoadError(RuntimeError):
    """Raised when no servable model version can be resolved from the registry."""


class ModelState:
    """Mutable holder for the currently loaded model, set once at startup."""

    def __init__(self) -> None:
        self.model: object | None = None
        self.model_version: str | None = None
        self.model_source: str | None = None
        self.load_error: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.model is not None


model_state = ModelState()


def resolve_and_load_model() -> tuple[object, str, str]:
    """Resolve the servable model version and load it. Raises ModelLoadError on failure."""
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    client = mlflow.MlflowClient()

    version: str | None = None
    source: str | None = None
    uri: str | None = None

    try:
        mv = client.get_model_version_by_alias(settings.model_name, settings.champion_alias)
        uri = f"models:/{settings.model_name}@{settings.champion_alias}"
        version, source = str(mv.version), f"alias:{settings.champion_alias}"
    except Exception:
        try:
            latest = client.get_latest_versions(
                settings.model_name, stages=[settings.legacy_stage_fallback]
            )
        except Exception as exc:
            raise ModelLoadError(
                f"Could not resolve model '{settings.model_name}' via alias "
                f"'{settings.champion_alias}' or stage '{settings.legacy_stage_fallback}': {exc}"
            ) from exc
        if not latest:
            raise ModelLoadError(
                f"No model version found for '{settings.model_name}' under alias "
                f"'{settings.champion_alias}' or stage '{settings.legacy_stage_fallback}'. "
                "Train a model and run `python -m src.registry.promote` first."
            ) from None
        uri = f"models:/{settings.model_name}/{settings.legacy_stage_fallback}"
        version, source = str(latest[0].version), f"stage:{settings.legacy_stage_fallback}"

    pyfunc_model = mlflow.pyfunc.load_model(uri)
    # Unwrap to the raw FraudModelWrapper and call it directly. mlflow's
    # PyFuncModel.predict() enforces the logged input *schema* strictly
    # (rejects int64-vs-float64 mismatches, etc) before our code ever runs
    # — which defeats the whole point of the ColumnAligner step tolerating
    # missing/extra/reordered/loosely-typed columns. The signature is still
    # logged (for the MLflow UI / other tooling) but is not used to gate
    # requests here.
    model = pyfunc_model.unwrap_python_model()
    return model, version, source


def load_model_into_state() -> None:
    from src.api.metrics import MODEL_INFO, MODEL_LOADED

    try:
        model, version, source = resolve_and_load_model()
        model_state.model = model
        model_state.model_version = version
        model_state.model_source = source
        model_state.load_error = None
        MODEL_LOADED.set(1)
        MODEL_INFO.labels(model_name=settings.model_name, model_version=version).set(1)
        logger.info(
            "model_loaded",
            extra={"model_name": settings.model_name, "model_version": version},
        )
    except Exception as exc:  # noqa: BLE001 - intentionally broad: startup must never crash the app
        model_state.model = None
        model_state.load_error = str(exc)
        MODEL_LOADED.set(0)
        logger.warning("model_load_failed: %s", exc)


def require_model() -> object:
    """FastAPI dependency: return the loaded model or raise a clean 503."""
    if not model_state.is_ready:
        raise HTTPException(
            status_code=503,
            detail="Model not loaded. Train and promote a model, then restart the service.",
        )
    return model_state.model
