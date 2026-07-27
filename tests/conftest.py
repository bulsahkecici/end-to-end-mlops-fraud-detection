from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.config import settings
from src.data.ingest import make_synthetic_transactions

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "ieee-fraud-detection"


@pytest.fixture
def mlflow_tmp_uri(tmp_path: Path) -> str:
    """An isolated, file-backed MLflow tracking+registry store per test."""
    return f"sqlite:///{tmp_path / 'mlflow.db'}"


@pytest.fixture
def patch_mlflow_uri(monkeypatch: pytest.MonkeyPatch, mlflow_tmp_uri: str) -> str:
    """Point the shared ``settings`` singleton at an isolated MLflow store.

    Needed because the API's model loader reads ``settings.mlflow_tracking_uri``
    at call time (not via a function argument), unlike ``run_training``.
    """
    monkeypatch.setattr(settings, "mlflow_tracking_uri", mlflow_tmp_uri)
    return mlflow_tmp_uri


@pytest.fixture
def synthetic_df() -> pd.DataFrame:
    return make_synthetic_transactions(n=800, seed=123, fraud_rate=0.08)


@pytest.fixture
def fixture_data_dir() -> Path:
    return FIXTURES_DIR
