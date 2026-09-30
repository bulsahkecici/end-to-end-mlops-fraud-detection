"""Pydantic request/response schemas for the fraud-detection API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.config import settings


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        max_length=settings.api_max_batch_size,
        description="One or more transaction records. Record-level extra fields are ignored; "
        "recognized model features are validated against the loaded model schema.",
    )


class PredictionItem(BaseModel):
    fraud_probability: float
    fraud_prediction: int
    threshold: float


class PredictResponse(BaseModel):
    predictions: list[PredictionItem]
    model_name: str
    model_version: str | None = None


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    model_name: str
    model_version: str | None = None
    model_source: str | None = None
    run_id: str | None = None
    deployed_at: str | None = None
