"""Pydantic request/response schemas for the fraud-detection API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.config import settings


class PredictRequest(BaseModel):
    records: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        max_length=settings.api_max_batch_size,
        description="One or more transaction records. Missing/extra/reordered "
        "fields relative to the training schema are handled automatically.",
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
