"""Centralized configuration and path resolution.

This module is the single source of truth for the project root and every
derived path (data, artifacts, reports). Every other module MUST import
``PROJECT_ROOT`` / ``settings`` from here instead of recomputing
``Path(__file__).resolve().parents[...]`` locally — that pattern is what
previously caused the training pipeline and the serving pipeline to look at
two different ``artifacts`` directories.

Required environment variables are validated eagerly so misconfiguration
fails fast and loudly instead of silently falling back to a wrong default.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# src/config.py -> parent = src/, parent.parent = repo root.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name}={value!r} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name}={value!r} must be a float") from exc


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Process-wide configuration singleton (see ``settings`` below).

    Deliberately *not* frozen: tests monkeypatch individual attributes (e.g.
    ``mlflow_tracking_uri``) on the shared ``settings`` instance to point at
    an isolated per-test MLflow store, since env vars are only read once at
    import time and re-importing this module per test is not practical.
    """

    # --- paths -----------------------------------------------------------
    project_root: Path = PROJECT_ROOT
    data_dir: Path = field(init=False)
    raw_data_dir: Path = field(init=False)
    processed_data_dir: Path = field(init=False)
    ieee_data_dir: Path = field(init=False)
    artifacts_dir: Path = field(init=False)
    reports_dir: Path = field(init=False)

    # --- mlflow ------------------------------------------------------------
    mlflow_tracking_uri: str = field(default_factory=lambda: _env_str(
        "MLFLOW_TRACKING_URI", "http://localhost:5000"
    ))
    experiment_name: str = field(default_factory=lambda: _env_str(
        "MLFLOW_EXPERIMENT_NAME", "ieee-cis-fraud"
    ))
    model_name: str = field(default_factory=lambda: _env_str(
        "MODEL_NAME", "ieee_fraud_lgbm"
    ))
    champion_alias: str = field(default_factory=lambda: _env_str(
        "CHAMPION_ALIAS", "champion"
    ))
    candidate_alias: str = field(default_factory=lambda: _env_str(
        "CANDIDATE_ALIAS", "candidate"
    ))
    legacy_stage_fallback: str = field(default_factory=lambda: _env_str(
        "LEGACY_PROD_STAGE", "Production"
    ))

    # --- sampling ------------------------------------------------------------
    sample_rows: int | None = field(default_factory=lambda: _env_int("SAMPLE_ROWS", 300_000) or None)
    sampling_strategy: str = field(default_factory=lambda: _env_str(
        "SAMPLING_STRATEGY", "time_ordered"
    ))
    random_seed: int = field(default_factory=lambda: _env_int("RANDOM_SEED", 42))

    # --- split ------------------------------------------------------------
    train_ratio: float = field(default_factory=lambda: _env_float("TRAIN_RATIO", 0.70))
    val_ratio: float = field(default_factory=lambda: _env_float("VAL_RATIO", 0.15))
    test_ratio: float = field(default_factory=lambda: _env_float("TEST_RATIO", 0.15))
    split_strategy: str = field(default_factory=lambda: _env_str(
        "SPLIT_STRATEGY", "temporal"
    ))

    # --- threshold / cost --------------------------------------------------
    threshold_strategy: str = field(default_factory=lambda: _env_str(
        "THRESHOLD_STRATEGY", "best_f1"
    ))
    fixed_threshold: float = field(default_factory=lambda: _env_float("FIXED_THRESHOLD", 0.5))
    target_recall: float = field(default_factory=lambda: _env_float("TARGET_RECALL", 0.80))
    false_negative_cost: float = field(default_factory=lambda: _env_float("FALSE_NEGATIVE_COST", 25.0))
    false_positive_cost: float = field(default_factory=lambda: _env_float("FALSE_POSITIVE_COST", 1.0))

    # --- promotion gates -----------------------------------------------------
    min_pr_auc: float = field(default_factory=lambda: _env_float("MIN_PR_AUC", 0.10))
    min_recall: float = field(default_factory=lambda: _env_float("MIN_RECALL", 0.10))
    max_champion_regression: float = field(default_factory=lambda: _env_float(
        "MAX_CHAMPION_REGRESSION", 0.02
    ))

    # --- API ------------------------------------------------------------
    api_max_batch_size: int = field(default_factory=lambda: _env_int("API_MAX_BATCH_SIZE", 500))
    api_max_request_bytes: int = field(default_factory=lambda: _env_int(
        "API_MAX_REQUEST_BYTES", 2_000_000
    ))
    api_key: str | None = field(default_factory=lambda: os.environ.get("API_KEY") or None)
    api_key_enabled: bool = field(default_factory=lambda: _env_bool("API_KEY_ENABLED", False))
    cors_allow_origins: str = field(default_factory=lambda: _env_str("CORS_ALLOW_ORIGINS", "*"))

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_dir", self.project_root / "data")
        object.__setattr__(self, "raw_data_dir", self.data_dir / "raw")
        object.__setattr__(self, "processed_data_dir", self.data_dir / "processed")
        object.__setattr__(
            self, "ieee_data_dir", self.processed_data_dir / "ieee-fraud-detection"
        )
        object.__setattr__(self, "artifacts_dir", self.project_root / "artifacts")
        object.__setattr__(self, "reports_dir", self.project_root / "reports")

        if self.api_key_enabled and not self.api_key:
            raise ConfigError(
                "API_KEY_ENABLED=true requires API_KEY to be set."
            )
        ratios = (self.train_ratio, self.val_ratio, self.test_ratio)
        if any(r <= 0 for r in ratios) or abs(sum(ratios) - 1.0) > 1e-6:
            raise ConfigError(
                f"TRAIN_RATIO+VAL_RATIO+TEST_RATIO must sum to 1.0, got {ratios}"
            )


settings = Settings()
