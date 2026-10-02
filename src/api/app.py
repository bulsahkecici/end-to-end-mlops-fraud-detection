"""FastAPI inference service for the IEEE-CIS fraud detection model.

Endpoints:
    GET  /health   - liveness: process is up.
    GET  /ready    - readiness: a model is loaded and servable.
    POST /predict  - fraud probability + thresholded decision for one or more records.
    GET  /metrics  - Prometheus metrics.

The model is loaded from the immutable MLflow version in explicit deployment
state at startup (see ``src/api/dependencies.py``); there is no alias-following
or local feature_meta.json dependency, so training and serving use the same
fitted preprocessing pipeline.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Response

from src.api.dependencies import load_model_into_state, model_state, require_model
from src.api.metrics import (
    CONTENT_TYPE_LATEST,
    FRAUD_PREDICTIONS_TOTAL,
    PREDICT_BATCH_SIZE,
    PREDICTION_EXCEPTIONS_TOTAL,
    PREDICTIONS_TOTAL,
    READINESS_FAILURES_TOTAL,
    SEMANTIC_VALIDATION_FAILURES_TOTAL,
    render_latest,
    semantic_validation_reason_label,
)
from src.api.middleware import register_middleware, setup_cors
from src.api.schemas import (
    HealthResponse,
    PredictionItem,
    PredictRequest,
    PredictResponse,
    ReadyResponse,
)
from src.api.validation import SemanticValidationError, validate_records
from src.config import settings
from src.logging_config import configure_logging

configure_logging()
logger = logging.getLogger("src.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_model_into_state()
    yield


app = FastAPI(title="IEEE Fraud Detection API", version="1.0.0", lifespan=lifespan)
setup_cors(app)
register_middleware(app)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/ready", response_model=ReadyResponse)
def ready() -> ReadyResponse:
    if not model_state.is_ready:
        READINESS_FAILURES_TOTAL.inc()
        raise HTTPException(
            status_code=503,
            detail="Model is not ready. Check service logs and deployment state.",
        )
    return ReadyResponse(
        status="ready",
        model_name=settings.model_name,
        model_version=model_state.model_version,
        model_source=model_state.model_source,
        run_id=model_state.run_id,
        deployed_at=model_state.deployed_at,
    )


@app.get("/metrics")
def metrics() -> Response:
    return Response(content=render_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/predict", response_model=PredictResponse)
def predict(body: PredictRequest, model=Depends(require_model)) -> PredictResponse:
    PREDICT_BATCH_SIZE.observe(len(body.records))
    feature_contract = model_state.feature_contract
    if feature_contract is None:
        logger.error("model_feature_contract_unavailable")
        raise HTTPException(status_code=503, detail="Model inference contract is unavailable")

    try:
        validate_records(body.records, feature_contract)
    except SemanticValidationError as exc:
        reason = semantic_validation_reason_label(issue.code for issue in exc.issues)
        SEMANTIC_VALIDATION_FAILURES_TOTAL.labels(code=reason).inc()
        raise HTTPException(
            status_code=422,
            detail={
                "code": "semantic_validation_failed",
                "message": "One or more records failed semantic validation.",
                "errors": [issue.as_dict() for issue in exc.issues],
            },
        ) from None

    try:
        df = pd.DataFrame(body.records)
        output = model.predict(context=None, model_input=df)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - never leak a raw pipeline traceback to the client
        PREDICTION_EXCEPTIONS_TOTAL.inc()
        logger.error(
            "prediction_failed",
            extra={"batch_size": len(body.records), "error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=500, detail="Prediction failed") from exc

    predictions = [
        PredictionItem(
            fraud_probability=float(row["fraud_probability"]),
            fraud_prediction=int(row["fraud_prediction"]),
            threshold=float(row["threshold"]),
        )
        for _, row in output.iterrows()
    ]
    PREDICTIONS_TOTAL.inc(len(predictions))
    FRAUD_PREDICTIONS_TOTAL.inc(sum(p.fraud_prediction for p in predictions))
    return PredictResponse(
        predictions=predictions,
        model_name=settings.model_name,
        model_version=model_state.model_version,
    )
