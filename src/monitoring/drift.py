"""Deterministic, reference-authoritative feature and prediction drift reports.

Current data never decides feature roles, numeric bins, or categorical buckets.
Volatile generation time is kept outside the deterministic ``semantic`` payload.
The CLI exits 0 for PASS/WARN, 1 for BREACH, and 2 for invalid input/contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.deployment.lifecycle import DeploymentStateError, load_deployment_state

REPORT_SCHEMA_VERSION = "1.0"
CONTRACT_SCHEMA_VERSION = "1.0"
PASS = "PASS"
WARN = "WARN"
BREACH = "BREACH"
NOT_EVALUATED = "NOT_EVALUATED"
VALID_CHECK_STATUSES = frozenset({PASS, WARN, BREACH, NOT_EVALUATED})
_SEVERITY = {PASS: "INFO", WARN: "WARNING", BREACH: "ERROR", NOT_EVALUATED: "INFO"}
_OTHER = "OTHER"
_MISSING = "MISSING"
_UNKNOWN = "UNKNOWN"
_MAX_EXTRA_COLUMNS = 20


class MonitoringContractError(ValueError):
    """Monitoring cannot proceed without guessing."""


@dataclass(frozen=True)
class DriftThresholds:
    """Fixed thresholds; none are selected from current data."""

    missing_rate_warn: float = 0.05
    missing_rate_breach: float = 0.10
    invalid_rate_warn: float = 0.01
    invalid_rate_breach: float = 0.05
    distribution_warn: float = 0.10
    distribution_breach: float = 0.20
    standardized_mean_warn: float = 0.25
    standardized_mean_breach: float = 0.50
    unknown_rate_warn: float = 0.01
    unknown_rate_breach: float = 0.05
    prediction_mean_warn: float = 0.05
    prediction_mean_breach: float = 0.10
    positive_rate_warn: float = 0.05
    positive_rate_breach: float = 0.10

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> DriftThresholds:
        if payload is None:
            result = cls()
        else:
            extra = sorted(set(payload) - set(cls.__dataclass_fields__))
            if extra:
                raise MonitoringContractError(
                    "thresholds contain unexpected fields: " + ", ".join(extra)
                )
            try:
                result = cls(**{key: float(value) for key, value in payload.items()})
            except (TypeError, ValueError) as exc:
                raise MonitoringContractError("thresholds must be numeric") from exc
        result.validate()
        return result

    def validate(self) -> None:
        for name, warn, breach in (
            ("missing_rate", self.missing_rate_warn, self.missing_rate_breach),
            ("invalid_rate", self.invalid_rate_warn, self.invalid_rate_breach),
            ("distribution", self.distribution_warn, self.distribution_breach),
            ("standardized_mean", self.standardized_mean_warn, self.standardized_mean_breach),
            ("unknown_rate", self.unknown_rate_warn, self.unknown_rate_breach),
            ("prediction_mean", self.prediction_mean_warn, self.prediction_mean_breach),
            ("positive_rate", self.positive_rate_warn, self.positive_rate_breach),
        ):
            if not (math.isfinite(warn) and math.isfinite(breach) and 0 <= warn <= breach):
                raise MonitoringContractError(
                    f"{name} thresholds must satisfy finite 0 <= warn <= breach"
                )


@dataclass(frozen=True)
class MonitoringSchema:
    """Feature roles copied from authoritative fitted-model metadata."""

    numeric_features: tuple[str, ...]
    categorical_features: tuple[str, ...]
    numeric_bin_count: int = 10
    categorical_top_k: int = 10
    probability_column: str | None = None
    decision_column: str | None = None

    @classmethod
    def from_dict(
        cls, feature_schema: Mapping[str, Any], monitoring: Mapping[str, Any] | None = None
    ) -> MonitoringSchema:
        monitoring = monitoring or {}
        numeric = _field_names(feature_schema, "numeric_cols")
        categorical = _field_names(feature_schema, "categorical_cols")
        if set(numeric) & set(categorical):
            raise MonitoringContractError("feature schema assigns duplicate feature roles")
        if not numeric and not categorical:
            raise MonitoringContractError("feature schema contains no model features")
        allowed = {
            "numeric_bin_count",
            "categorical_top_k",
            "probability_column",
            "decision_column",
        }
        extra = sorted(set(monitoring) - allowed)
        if extra:
            raise MonitoringContractError(
                "monitoring configuration contains unexpected fields: " + ", ".join(extra)
            )
        try:
            bin_count = int(monitoring.get("numeric_bin_count", 10))
            top_k = int(monitoring.get("categorical_top_k", 10))
        except (TypeError, ValueError) as exc:
            raise MonitoringContractError("monitoring bucket limits must be integers") from exc
        probability = _optional_name(monitoring.get("probability_column"))
        decision = _optional_name(monitoring.get("decision_column"))
        if not 2 <= bin_count <= 50:
            raise MonitoringContractError("numeric_bin_count must be between 2 and 50")
        if not 1 <= top_k <= 50:
            raise MonitoringContractError("categorical_top_k must be between 1 and 50")
        model_names = set(numeric) | set(categorical)
        prediction_names = [name for name in (probability, decision) if name is not None]
        if len(prediction_names) != len(set(prediction_names)) or model_names & set(
            prediction_names
        ):
            raise MonitoringContractError(
                "prediction columns must be distinct from each other and model features"
            )
        return cls(
            tuple(sorted(numeric)),
            tuple(sorted(categorical)),
            bin_count,
            top_k,
            probability,
            decision,
        )

    @property
    def feature_names(self) -> tuple[str, ...]:
        return self.numeric_features + self.categorical_features

    @property
    def authorized_columns(self) -> frozenset[str]:
        predictions = tuple(
            value for value in (self.probability_column, self.decision_column) if value is not None
        )
        return frozenset(self.feature_names + predictions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "numeric_features": list(self.numeric_features),
            "categorical_features": list(self.categorical_features),
            "numeric_bin_count": self.numeric_bin_count,
            "categorical_top_k": self.categorical_top_k,
            "probability_column": self.probability_column,
            "decision_column": self.decision_column,
        }


@dataclass(frozen=True)
class MonitoringProvenance:
    model_name: str
    model_version: str
    run_id: str
    deployment_id: str | None = None
    threshold: float | None = None
    threshold_source: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> MonitoringProvenance:
        allowed = {
            "model_name",
            "model_version",
            "run_id",
            "deployment_id",
            "threshold",
            "threshold_source",
        }
        extra = sorted(set(payload) - allowed)
        if extra:
            raise MonitoringContractError(
                "model provenance contains unexpected fields: " + ", ".join(extra)
            )
        missing = [
            name
            for name in ("model_name", "model_version", "run_id")
            if not _nonempty(payload.get(name))
        ]
        if missing:
            raise MonitoringContractError(
                "model provenance is missing required fields: " + ", ".join(missing)
            )
        deployment_id = _optional_name(payload.get("deployment_id"))
        threshold_raw = payload.get("threshold")
        threshold_source = _optional_name(payload.get("threshold_source"))
        if (threshold_raw is None) != (threshold_source is None):
            raise MonitoringContractError(
                "threshold and threshold_source must both be supplied or both be null"
            )
        threshold = None
        if threshold_raw is not None:
            try:
                threshold = float(threshold_raw)
            except (TypeError, ValueError) as exc:
                raise MonitoringContractError("threshold must be numeric") from exc
            if not math.isfinite(threshold) or not 0 <= threshold <= 1:
                raise MonitoringContractError("threshold must be finite and between 0 and 1")
        return cls(
            str(payload["model_name"]),
            str(payload["model_version"]),
            str(payload["run_id"]),
            deployment_id,
            threshold,
            threshold_source,
        )


@dataclass(frozen=True)
class MonitoringContract:
    schema: MonitoringSchema
    provenance: MonitoringProvenance
    thresholds: DriftThresholds

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> MonitoringContract:
        allowed = {
            "contract_schema_version",
            "model",
            "feature_schema",
            "monitoring",
            "thresholds",
        }
        extra = sorted(set(payload) - allowed)
        if extra:
            raise MonitoringContractError(
                "monitoring contract contains unexpected fields: " + ", ".join(extra)
            )
        if payload.get("contract_schema_version") != CONTRACT_SCHEMA_VERSION:
            raise MonitoringContractError(
                f"contract_schema_version must be {CONTRACT_SCHEMA_VERSION!r}"
            )
        model = payload.get("model")
        feature_schema = payload.get("feature_schema")
        monitoring = payload.get("monitoring", {})
        thresholds = payload.get("thresholds")
        if not isinstance(model, Mapping):
            raise MonitoringContractError("monitoring contract requires a model object")
        if not isinstance(feature_schema, Mapping):
            raise MonitoringContractError("monitoring contract requires a feature_schema object")
        if not isinstance(monitoring, Mapping):
            raise MonitoringContractError("monitoring must be an object")
        if thresholds is not None and not isinstance(thresholds, Mapping):
            raise MonitoringContractError("thresholds must be an object")
        return cls(
            MonitoringSchema.from_dict(feature_schema, monitoring),
            MonitoringProvenance.from_dict(model),
            DriftThresholds.from_dict(thresholds),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_schema_version": CONTRACT_SCHEMA_VERSION,
            "model": asdict(self.provenance),
            "feature_schema": {
                "numeric_cols": list(self.schema.numeric_features),
                "categorical_cols": list(self.schema.categorical_features),
            },
            "monitoring": {
                "numeric_bin_count": self.schema.numeric_bin_count,
                "categorical_top_k": self.schema.categorical_top_k,
                "probability_column": self.schema.probability_column,
                "decision_column": self.schema.decision_column,
            },
            "thresholds": asdict(self.thresholds),
        }


@dataclass(frozen=True)
class MonitoringWindow:
    start: str
    end: str

    @classmethod
    def from_values(cls, start: str, end: str, name: str) -> MonitoringWindow:
        parsed_start = _window_time(start, f"{name} window start")
        parsed_end = _window_time(end, f"{name} window end")
        if parsed_start > parsed_end:
            raise MonitoringContractError(f"{name} window start must not be after its end")
        return cls(parsed_start.isoformat(), parsed_end.isoformat())


def _field_names(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    values = payload.get(key)
    if not isinstance(values, list) or any(not _nonempty(value) for value in values):
        raise MonitoringContractError(f"feature_schema.{key} must be non-empty strings")
    if len(values) != len(set(values)):
        raise MonitoringContractError(f"feature_schema.{key} contains duplicates")
    return tuple(str(value) for value in values)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _optional_name(value: Any) -> str | None:
    if value is None:
        return None
    if not _nonempty(value):
        raise MonitoringContractError("optional names must be non-empty strings or null")
    return str(value)


def _window_time(value: str, label: str) -> datetime:
    if not _nonempty(value):
        raise MonitoringContractError(f"{label} must be an ISO-8601 timestamp")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MonitoringContractError(f"{label} must be an ISO-8601 timestamp") from exc
    if result.tzinfo is None:
        raise MonitoringContractError(f"{label} must include a timezone")
    return result.astimezone(UTC)


def load_monitoring_contract(path: Path) -> MonitoringContract:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MonitoringContractError(f"monitoring contract does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitoringContractError(f"could not read monitoring contract: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise MonitoringContractError("monitoring contract must be a JSON object")
    return MonitoringContract.from_dict(payload)


def apply_deployment_state(contract: MonitoringContract, path: Path) -> MonitoringContract:
    """Cross-check immutable identity and add the validated deployment ID."""
    try:
        state = load_deployment_state(path)
    except DeploymentStateError as exc:
        raise MonitoringContractError(str(exc)) from exc
    expected = contract.provenance
    if (state.model_name, state.model_version, state.run_id) != (
        expected.model_name,
        expected.model_version,
        expected.run_id,
    ):
        raise MonitoringContractError("deployment state identity does not match contract")
    if expected.deployment_id is not None and expected.deployment_id != state.deployment_id:
        raise MonitoringContractError("deployment ID does not match contract")
    return replace(contract, provenance=replace(expected, deployment_id=state.deployment_id))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _canonical_scalar(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "missing"}
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        missing = False
    if isinstance(missing, bool | np.bool_) and bool(missing):
        return {"type": "missing"}
    if isinstance(value, bool | np.bool_):
        return {"type": "boolean", "value": bool(value)}
    if isinstance(value, int | float | np.integer | np.floating):
        numeric = float(value)
        if math.isnan(numeric):
            return {"type": "nonfinite", "value": "nan"}
        if math.isinf(numeric):
            return {
                "type": "nonfinite",
                "value": "positive_inf" if numeric > 0 else "negative_inf",
            }
        return {"type": "number", "value": format(numeric, ".17g")}
    if isinstance(value, datetime | pd.Timestamp):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, np.datetime64):
        return {"type": "datetime", "value": pd.Timestamp(value).isoformat()}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": value.hex()}
    raise MonitoringContractError(
        "unsupported input value type for deterministic canonicalization: "
        f"{type(value).__name__}"
    )


def _validate_frame(frame: pd.DataFrame, name: str) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise MonitoringContractError(f"{name} must be a DataFrame")
    if frame.empty:
        raise MonitoringContractError(f"{name} must contain at least one row")
    if any(not isinstance(column, str) or not column for column in frame.columns):
        raise MonitoringContractError(f"{name} column names must be non-empty strings")
    if frame.columns.duplicated().any():
        raise MonitoringContractError(f"{name} contains duplicate columns")


def dataframe_fingerprint(frame: pd.DataFrame) -> str:
    """Hash columns and the sorted multiset of canonical rows, excluding index/order/layout."""
    _validate_frame(frame, "fingerprinted input")
    columns = sorted(frame.columns)
    rows = [
        _canonical_json([_canonical_scalar(value) for value in row])
        for row in frame.loc[:, columns].itertuples(index=False, name=None)
    ]
    digest = hashlib.sha256()
    digest.update(_canonical_json({"columns": columns, "rows": len(frame)}).encode())
    for row in sorted(rows):
        digest.update(b"\n")
        digest.update(row.encode())
    return digest.hexdigest()


def _safe(value: float | int | np.number | None) -> float | int | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return int(value) if isinstance(value, int | np.integer) else float(value)


def _numeric_values(series: pd.Series) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    missing = series.isna().to_numpy(dtype=bool)
    coerced = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float, na_value=np.nan)
    finite = np.isfinite(coerced)
    return coerced, finite, ~missing & ~finite


def _numeric_summary(series: pd.Series) -> dict[str, Any]:
    coerced, finite_mask, invalid_mask = _numeric_values(series)
    finite = np.sort(coerced[finite_mask])
    mean: float | None
    std: float | None
    minimum: float | None
    maximum: float | None
    quantiles: Sequence[float | None]
    if len(finite):
        quantiles = np.quantile(finite, [0.05, 0.5, 0.95], method="linear")
        mean, std = float(np.mean(finite)), float(np.std(finite, ddof=0))
        minimum, maximum = float(finite[0]), float(finite[-1])
    else:
        quantiles = [None, None, None]
        mean = std = minimum = maximum = None
    return {
        "rows": len(series),
        "finite_count": int(finite_mask.sum()),
        "finite_rate": float(finite_mask.mean()),
        "missing_rate": float(series.isna().mean()),
        "invalid_rate": float(invalid_mask.mean()),
        "mean": _safe(mean),
        "std": _safe(std),
        "min": _safe(minimum),
        "max": _safe(maximum),
        "quantiles": {
            "q05": _safe(quantiles[0]),
            "q50": _safe(quantiles[1]),
            "q95": _safe(quantiles[2]),
        },
    }


def _reference_bins(reference: pd.Series, bin_count: int) -> dict[str, Any]:
    values, finite_mask, _ = _numeric_values(reference)
    finite = np.sort(values[finite_mask])
    if not len(finite):
        return {"strategy": "unavailable_all_nonfinite", "edges": [], "constant": None}
    if finite[0] == finite[-1]:
        return {"strategy": "constant_reference", "edges": [], "constant": float(finite[0])}
    probabilities = np.linspace(0, 1, bin_count + 1)[1:-1]
    edges = np.unique(np.quantile(finite, probabilities, method="linear"))
    return {
        "strategy": "reference_quantiles",
        "edges": [_safe(edge) for edge in edges],
        "constant": None,
    }


def _numeric_buckets(series: pd.Series, bin_spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    values, finite_mask, _ = _numeric_values(series)
    finite = values[finite_mask]
    strategy = bin_spec["strategy"]
    if strategy == "unavailable_all_nonfinite":
        return []
    if strategy == "constant_reference":
        constant = float(bin_spec["constant"])
        counts = [
            int((finite < constant).sum()),
            int((finite == constant).sum()),
            int((finite > constant).sum()),
        ]
        names = ["BELOW_CONSTANT", "AT_CONSTANT", "ABOVE_CONSTANT"]
    else:
        edges = np.asarray(bin_spec["edges"], dtype=float)
        indexes = np.searchsorted(edges, finite, side="right")
        counts = [int((indexes == index).sum()) for index in range(len(edges) + 1)]
        names = [f"BIN_{index:02d}" for index in range(len(counts))]
    return [
        {
            "bucket": name,
            "count": count,
            "share": float(count / len(finite)) if len(finite) else 0.0,
        }
        for name, count in zip(names, counts, strict=True)
    ]


def _total_variation(
    reference: Sequence[Mapping[str, Any]], current: Sequence[Mapping[str, Any]]
) -> float:
    left = {str(item["bucket"]): float(item["share"]) for item in reference}
    right = {str(item["bucket"]): float(item["share"]) for item in current}
    buckets = sorted(set(left) | set(right))
    return 0.5 * math.fsum(
        abs(left.get(bucket, 0.0) - right.get(bucket, 0.0)) for bucket in buckets
    )


def _status(value: float, warn: float, breach: float) -> str:
    return BREACH if value >= breach else WARN if value >= warn else PASS


def _check(
    check_id: str,
    feature: str | None,
    metric: str,
    observed: Any,
    threshold: Any,
    status: str,
) -> dict[str, Any]:
    if status not in VALID_CHECK_STATUSES:
        raise AssertionError(f"invalid check status: {status}")
    return {
        "check_id": check_id,
        "feature": feature,
        "metric": metric,
        "observed": observed,
        "threshold": threshold,
        "status": status,
        "severity": _SEVERITY[status],
    }


def _rate_check(
    check_id: str,
    feature: str,
    metric: str,
    observed: float,
    warn: float,
    breach: float,
) -> dict[str, Any]:
    return _check(
        check_id,
        feature,
        metric,
        observed,
        {"warn": warn, "breach": breach},
        _status(observed, warn, breach),
    )


def _numeric_report(
    name: str,
    reference: pd.Series,
    current: pd.Series,
    present: bool,
    contract: MonitoringContract,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reference_summary = _numeric_summary(reference)
    current_summary = _numeric_summary(current)
    bins = _reference_bins(reference, contract.schema.numeric_bin_count)
    reference_buckets = _numeric_buckets(reference, bins)
    current_buckets = _numeric_buckets(current, bins)
    checks = [
        _check(
            f"feature.{name}.column_presence",
            name,
            "column_presence",
            present,
            True,
            PASS if present else BREACH,
        )
    ]
    missing_shift = abs(current_summary["missing_rate"] - reference_summary["missing_rate"])
    invalid_increase = max(0.0, current_summary["invalid_rate"] - reference_summary["invalid_rate"])
    checks += [
        _rate_check(
            f"feature.{name}.missing_rate_shift",
            name,
            "absolute_missing_rate_shift",
            missing_shift,
            contract.thresholds.missing_rate_warn,
            contract.thresholds.missing_rate_breach,
        ),
        _rate_check(
            f"feature.{name}.invalid_rate_increase",
            name,
            "invalid_rate_increase",
            invalid_increase,
            contract.thresholds.invalid_rate_warn,
            contract.thresholds.invalid_rate_breach,
        ),
    ]
    if reference_buckets:
        distribution_shift = _total_variation(reference_buckets, current_buckets)
        checks.append(
            _rate_check(
                f"feature.{name}.distribution_shift",
                name,
                "total_variation_distance",
                distribution_shift,
                contract.thresholds.distribution_warn,
                contract.thresholds.distribution_breach,
            )
        )
    else:
        distribution_shift = None
        checks.append(
            _check(
                f"feature.{name}.distribution_shift",
                name,
                "total_variation_distance",
                None,
                {
                    "warn": contract.thresholds.distribution_warn,
                    "breach": contract.thresholds.distribution_breach,
                },
                NOT_EVALUATED,
            )
        )
    ref_mean, ref_std = reference_summary["mean"], reference_summary["std"]
    current_mean = current_summary["mean"]
    if ref_mean is not None and ref_std is not None and ref_std > 0 and current_mean is not None:
        mean_shift = abs(current_mean - ref_mean) / ref_std
        checks.append(
            _rate_check(
                f"feature.{name}.standardized_mean_shift",
                name,
                "standardized_mean_shift",
                mean_shift,
                contract.thresholds.standardized_mean_warn,
                contract.thresholds.standardized_mean_breach,
            )
        )
    else:
        mean_shift = None
        checks.append(
            _check(
                f"feature.{name}.standardized_mean_shift",
                name,
                "standardized_mean_shift",
                None,
                {
                    "warn": contract.thresholds.standardized_mean_warn,
                    "breach": contract.thresholds.standardized_mean_breach,
                },
                NOT_EVALUATED,
            )
        )
    return (
        {
            "feature": name,
            "type": "numeric",
            "current_column_present": present,
            "reference": reference_summary,
            "current": current_summary,
            "reference_bins": bins,
            "distribution": {
                "reference_buckets": reference_buckets,
                "current_buckets": current_buckets,
                "total_variation_distance": _safe(distribution_shift),
            },
            "standardized_mean_shift": _safe(mean_shift),
        },
        checks,
    )


def _category_key(value: Any) -> str | None:
    try:
        if value is None or bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    return _canonical_json(_canonical_scalar(value))


def _category_token(key: str) -> str:
    return "CATEGORY_" + hashlib.sha256(key.encode()).hexdigest()


def _category_buckets(
    series: pd.Series, top_keys: Sequence[str], reference_keys: frozenset[str]
) -> list[dict[str, Any]]:
    top_map = {key: _category_token(key) for key in top_keys}
    order = [top_map[key] for key in top_keys] + [_OTHER, _MISSING, _UNKNOWN]
    counts = Counter({bucket: 0 for bucket in order})
    for value in series:
        key = _category_key(value)
        if key is None:
            bucket = _MISSING
        elif key in top_map:
            bucket = top_map[key]
        elif key in reference_keys:
            bucket = _OTHER
        else:
            bucket = _UNKNOWN
        counts[bucket] += 1
    return [
        {"bucket": bucket, "count": counts[bucket], "share": counts[bucket] / len(series)}
        for bucket in order
    ]


def _categorical_report(
    name: str,
    reference: pd.Series,
    current: pd.Series,
    present: bool,
    contract: MonitoringContract,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reference_values = [key for value in reference if (key := _category_key(value)) is not None]
    counts = Counter(reference_values)
    ranked = sorted(counts, key=lambda key: (-counts[key], key))
    top_keys = ranked[: contract.schema.categorical_top_k]
    reference_keys = frozenset(counts)
    reference_buckets = _category_buckets(reference, top_keys, reference_keys)
    current_buckets = _category_buckets(current, top_keys, reference_keys)
    distribution_shift = _total_variation(reference_buckets, current_buckets)
    ref_map = {item["bucket"]: item for item in reference_buckets}
    cur_map = {item["bucket"]: item for item in current_buckets}
    missing_shift = abs(cur_map[_MISSING]["share"] - ref_map[_MISSING]["share"])
    unknown_rate = cur_map[_UNKNOWN]["share"]
    checks = [
        _check(
            f"feature.{name}.column_presence",
            name,
            "column_presence",
            present,
            True,
            PASS if present else BREACH,
        ),
        _rate_check(
            f"feature.{name}.missing_rate_shift",
            name,
            "absolute_missing_rate_shift",
            missing_shift,
            contract.thresholds.missing_rate_warn,
            contract.thresholds.missing_rate_breach,
        ),
        _rate_check(
            f"feature.{name}.unknown_rate",
            name,
            "unknown_category_rate",
            unknown_rate,
            contract.thresholds.unknown_rate_warn,
            contract.thresholds.unknown_rate_breach,
        ),
        _rate_check(
            f"feature.{name}.distribution_shift",
            name,
            "total_variation_distance",
            distribution_shift,
            contract.thresholds.distribution_warn,
            contract.thresholds.distribution_breach,
        ),
    ]
    return (
        {
            "feature": name,
            "type": "categorical",
            "current_column_present": present,
            "reference_unique_categories": len(reference_keys),
            "top_category_limit": contract.schema.categorical_top_k,
            "category_tokens_are_sha256": True,
            "buckets": {
                "reference": reference_buckets,
                "current": current_buckets,
                "total_variation_distance": distribution_shift,
            },
        },
        checks,
    )


def _probability_report(
    reference: pd.Series,
    current: pd.Series,
    present: bool,
    contract: MonitoringContract,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    name = str(contract.schema.probability_column)
    ref_values, ref_finite, ref_invalid = _numeric_values(reference)
    cur_values, cur_finite, cur_invalid = _numeric_values(current)
    ref_outside = ref_finite & ((ref_values < 0) | (ref_values > 1))
    cur_outside = cur_finite & ((cur_values < 0) | (cur_values > 1))
    ref_valid = ref_finite & ~ref_outside
    cur_valid = cur_finite & ~cur_outside
    ref_valid_count = int(ref_valid.sum())
    cur_valid_count = int(cur_valid.sum())
    if ref_valid_count == 0:
        raise MonitoringContractError(
            "reference configured probability column contains no valid values"
        )
    ref_invalid_rate = float((ref_invalid | ref_outside).mean())
    cur_invalid_rate = float((cur_invalid | cur_outside).mean())
    ref_series = pd.Series(np.sort(ref_values[ref_valid]))
    cur_series = pd.Series(np.sort(cur_values[cur_valid]))
    bins = {
        "strategy": "fixed_probability_deciles",
        "edges": [index / 10 for index in range(1, 10)],
        "constant": None,
    }
    ref_buckets, cur_buckets = _numeric_buckets(ref_series, bins), _numeric_buckets(
        cur_series, bins
    )
    comparable = present and cur_valid_count > 0
    distribution_shift = _total_variation(ref_buckets, cur_buckets) if comparable else None
    ref_mean = float(ref_series.mean()) if len(ref_series) else None
    cur_mean = float(cur_series.mean()) if len(cur_series) else None
    mean_shift = abs(cur_mean - ref_mean) if ref_mean is not None and cur_mean is not None else None
    checks = [
        _check(
            "prediction.probability.column_presence",
            name,
            "column_presence",
            present,
            True,
            PASS if present else BREACH,
        ),
        _check(
            "prediction.probability.valid_data_availability",
            name,
            "valid_probability_count",
            cur_valid_count,
            {"minimum": 1},
            PASS if comparable else BREACH,
        ),
        _rate_check(
            "prediction.probability.invalid_rate_increase",
            name,
            "invalid_probability_rate_increase",
            max(0.0, cur_invalid_rate - ref_invalid_rate),
            contract.thresholds.invalid_rate_warn,
            contract.thresholds.invalid_rate_breach,
        ),
    ]
    if distribution_shift is None:
        checks.append(
            _check(
                "prediction.probability.distribution_shift",
                name,
                "total_variation_distance",
                None,
                {
                    "warn": contract.thresholds.distribution_warn,
                    "breach": contract.thresholds.distribution_breach,
                },
                NOT_EVALUATED,
            )
        )
    else:
        checks.append(
            _rate_check(
                "prediction.probability.distribution_shift",
                name,
                "total_variation_distance",
                distribution_shift,
                contract.thresholds.distribution_warn,
                contract.thresholds.distribution_breach,
            )
        )
    if mean_shift is None:
        checks.append(
            _check(
                "prediction.probability.mean_shift",
                name,
                "absolute_mean_probability_shift",
                None,
                {
                    "warn": contract.thresholds.prediction_mean_warn,
                    "breach": contract.thresholds.prediction_mean_breach,
                },
                NOT_EVALUATED,
            )
        )
    else:
        checks.append(
            _rate_check(
                "prediction.probability.mean_shift",
                name,
                "absolute_mean_probability_shift",
                mean_shift,
                contract.thresholds.prediction_mean_warn,
                contract.thresholds.prediction_mean_breach,
            )
        )
    return (
        {
            "status": "EVALUATED" if comparable else NOT_EVALUATED,
            "reason": None if comparable else "current_has_no_valid_probabilities",
            "column": name,
            "current_column_present": present,
            "reference_valid_count": ref_valid_count,
            "current_valid_count": cur_valid_count,
            "reference_missing_rate": float(reference.isna().mean()),
            "current_missing_rate": float(current.isna().mean()),
            "reference_mean_probability": _safe(ref_mean),
            "current_mean_probability": _safe(cur_mean),
            "reference_invalid_rate": ref_invalid_rate,
            "current_invalid_rate": cur_invalid_rate,
            "bins": bins,
            "reference_buckets": ref_buckets,
            "current_buckets": cur_buckets,
            "total_variation_distance": distribution_shift,
        },
        checks,
    )


def _decision_values(series: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float, na_value=np.nan)
    return values, np.isfinite(values) & np.isin(values, [0.0, 1.0])


def _decision_report(
    reference: pd.Series,
    current: pd.Series,
    present: bool,
    contract: MonitoringContract,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    name = str(contract.schema.decision_column)
    ref_values, ref_valid = _decision_values(reference)
    cur_values, cur_valid = _decision_values(current)
    ref_valid_count = int(ref_valid.sum())
    cur_valid_count = int(cur_valid.sum())
    if ref_valid_count == 0:
        raise MonitoringContractError(
            "reference configured decision column contains no valid values"
        )
    ref_rate = float(ref_values[ref_valid].mean()) if ref_valid.any() else None
    cur_rate = float(cur_values[cur_valid].mean()) if cur_valid.any() else None
    ref_invalid = float((~reference.isna().to_numpy() & ~ref_valid).mean())
    cur_invalid = float((~current.isna().to_numpy() & ~cur_valid).mean())
    comparable = present and cur_valid_count > 0
    shift = abs(cur_rate - ref_rate) if ref_rate is not None and cur_rate is not None else None
    checks = [
        _check(
            "prediction.decision.column_presence",
            name,
            "column_presence",
            present,
            True,
            PASS if present else BREACH,
        ),
        _check(
            "prediction.decision.valid_data_availability",
            name,
            "valid_decision_count",
            cur_valid_count,
            {"minimum": 1},
            PASS if comparable else BREACH,
        ),
        _rate_check(
            "prediction.decision.invalid_rate_increase",
            name,
            "invalid_decision_rate_increase",
            max(0.0, cur_invalid - ref_invalid),
            contract.thresholds.invalid_rate_warn,
            contract.thresholds.invalid_rate_breach,
        ),
    ]
    if shift is None:
        checks.append(
            _check(
                "prediction.decision.positive_rate_shift",
                name,
                "absolute_positive_decision_rate_shift",
                None,
                {
                    "warn": contract.thresholds.positive_rate_warn,
                    "breach": contract.thresholds.positive_rate_breach,
                },
                NOT_EVALUATED,
            )
        )
    else:
        checks.append(
            _rate_check(
                "prediction.decision.positive_rate_shift",
                name,
                "absolute_positive_decision_rate_shift",
                shift,
                contract.thresholds.positive_rate_warn,
                contract.thresholds.positive_rate_breach,
            )
        )
    return (
        {
            "status": "EVALUATED" if comparable else NOT_EVALUATED,
            "reason": None if comparable else "current_has_no_valid_decisions",
            "column": name,
            "current_column_present": present,
            "reference_valid_count": ref_valid_count,
            "current_valid_count": cur_valid_count,
            "reference_missing_rate": float(reference.isna().mean()),
            "current_missing_rate": float(current.isna().mean()),
            "reference_positive_decision_rate": _safe(ref_rate),
            "current_positive_decision_rate": _safe(cur_rate),
            "reference_invalid_rate": ref_invalid,
            "current_invalid_rate": cur_invalid,
        },
        checks,
    )


def _prediction_report(
    reference: pd.DataFrame, current: pd.DataFrame, contract: MonitoringContract
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    probability = contract.schema.probability_column
    decision = contract.schema.decision_column
    threshold = {
        "value": contract.provenance.threshold,
        "source": contract.provenance.threshold_source,
        "status": "AVAILABLE" if contract.provenance.threshold is not None else "UNAVAILABLE",
    }
    if probability is None and decision is None:
        return (
            {
                "status": NOT_EVALUATED,
                "reason": "prediction_columns_not_configured",
                "threshold": threshold,
                "probability": {"status": NOT_EVALUATED},
                "decisions": {"status": NOT_EVALUATED},
            },
            [
                _check(
                    "prediction.availability",
                    None,
                    "prediction_data_availability",
                    False,
                    True,
                    NOT_EVALUATED,
                )
            ],
        )
    checks: list[dict[str, Any]] = []
    if probability is not None:
        if probability not in reference:
            raise MonitoringContractError("reference is missing configured probability column")
        present = probability in current
        current_values = current[probability] if present else pd.Series([np.nan] * len(current))
        probability_report, new_checks = _probability_report(
            reference[probability], current_values, present, contract
        )
        checks += new_checks
    else:
        probability_report = {
            "status": NOT_EVALUATED,
            "reason": "probability_column_not_configured",
        }
    if decision is not None:
        if decision not in reference:
            raise MonitoringContractError("reference is missing configured decision column")
        present = decision in current
        current_values = current[decision] if present else pd.Series([np.nan] * len(current))
        decision_report, new_checks = _decision_report(
            reference[decision], current_values, present, contract
        )
        checks += new_checks
    else:
        decision_report = {"status": NOT_EVALUATED, "reason": "decision_column_not_configured"}
    configured_reports = [
        component
        for configured, component in (
            (probability is not None, probability_report),
            (decision is not None, decision_report),
        )
        if configured
    ]
    return (
        {
            "status": (
                "EVALUATED"
                if all(component["status"] == "EVALUATED" for component in configured_reports)
                else NOT_EVALUATED
            ),
            "threshold": threshold,
            "probability": probability_report,
            "decisions": decision_report,
        },
        checks,
    )


def _unexpected_columns(current: pd.DataFrame, allowed: frozenset[str]) -> dict[str, Any]:
    names = sorted(set(current.columns) - set(allowed))
    reported = names[:_MAX_EXTRA_COLUMNS]
    return {
        "count": len(names),
        "column_name_sha256": [hashlib.sha256(name.encode()).hexdigest() for name in reported],
        "reported_count": len(reported),
        "truncated": len(names) > len(reported),
    }


def compute_drift_report(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    *,
    contract: MonitoringContract,
    reference_source: str,
    current_source: str,
    reference_window: MonitoringWindow,
    current_window: MonitoringWindow,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Compute stable semantic content plus a separate generation timestamp."""
    _validate_frame(reference, "reference input")
    _validate_frame(current, "current input")
    if not _nonempty(reference_source) or not _nonempty(current_source):
        raise MonitoringContractError("reference/current source must be non-empty")
    missing_reference = sorted(set(contract.schema.feature_names) - set(reference.columns))
    if missing_reference:
        raise MonitoringContractError(
            "reference is missing model features: " + ", ".join(missing_reference)
        )
    features: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    for name in contract.schema.numeric_features:
        present = name in current
        series = current[name] if present else pd.Series([np.nan] * len(current))
        feature, new_checks = _numeric_report(name, reference[name], series, present, contract)
        features.append(feature)
        checks += new_checks
    for name in contract.schema.categorical_features:
        present = name in current
        series = current[name] if present else pd.Series([None] * len(current), dtype=object)
        feature, new_checks = _categorical_report(name, reference[name], series, present, contract)
        features.append(feature)
        checks += new_checks
    predictions, new_checks = _prediction_report(reference, current, contract)
    checks += new_checks
    unexpected = _unexpected_columns(current, contract.schema.authorized_columns)
    checks.append(
        _check(
            "schema.unexpected_current_columns",
            None,
            "unexpected_current_column_count",
            unexpected["count"],
            0,
            WARN if unexpected["count"] else PASS,
        )
    )
    checks.sort(key=lambda value: str(value["check_id"]))
    counts = {
        status: sum(check["status"] == status for check in checks)
        for status in sorted(VALID_CHECK_STATUSES)
    }
    overall = BREACH if counts[BREACH] else WARN if counts[WARN] else PASS
    schema_payload = contract.schema.as_dict()
    semantic = {
        "provenance": {
            **asdict(contract.provenance),
            "reference_source": reference_source,
            "current_source": current_source,
            "reference_window": asdict(reference_window),
            "current_window": asdict(current_window),
        },
        "inputs": {
            "reference": {
                "rows": len(reference),
                "fingerprint_sha256": dataframe_fingerprint(reference),
            },
            "current": {
                "rows": len(current),
                "fingerprint_sha256": dataframe_fingerprint(current),
            },
        },
        "monitoring_contract": {
            "schema_fingerprint_sha256": _sha256_json(schema_payload),
            "config_fingerprint_sha256": _sha256_json(contract.as_dict()),
            "schema": schema_payload,
            "thresholds": asdict(contract.thresholds),
        },
        "schema_observations": {"unexpected_current_columns": unexpected},
        "features": sorted(features, key=lambda value: (value["feature"], value["type"])),
        "prediction_distribution": predictions,
        "checks": checks,
        "summary": {
            "overall_status": overall,
            "exit_code": 1 if overall == BREACH else 0,
            "check_counts": counts,
        },
    }
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "semantic_identity_sha256": _sha256_json(semantic),
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "semantic": semantic,
    }
    _canonical_json(report)  # strict no-NaN/Infinity validation boundary
    return report


def report_exit_code(report: Mapping[str, Any]) -> int:
    return int(report["semantic"]["summary"]["exit_code"])


def render_markdown(report: Mapping[str, Any]) -> str:
    semantic = report["semantic"]
    provenance = semantic["provenance"]
    summary = semantic["summary"]
    lines = [
        "# Drift report",
        "",
        f"Report schema: {report['report_schema_version']}",
        f"Semantic identity: `{report['semantic_identity_sha256']}`",
        f"Generated at: {report['generated_at']}",
        f"Model: `{provenance['model_name']}` version `{provenance['model_version']}`",
        f"Run ID: `{provenance['run_id']}`",
        f"Deployment ID: `{provenance['deployment_id'] or 'unavailable'}`",
        "",
        f"**Overall status:** {summary['overall_status']}",
        "",
        "| feature | type | present | distribution shift |",
        "|---|---|---:|---:|",
    ]
    for feature in semantic["features"]:
        distribution = (
            feature["distribution"] if feature["type"] == "numeric" else feature["buckets"]
        )
        shift = distribution["total_variation_distance"]
        rendered = "not evaluated" if shift is None else f"{shift:.6f}"
        lines.append(
            f"| {feature['feature']} | {feature['type']} | "
            f"{feature['current_column_present']} | {rendered} |"
        )
    prediction = semantic["prediction_distribution"]
    lines += ["", "## Prediction distribution", "", f"Status: {prediction['status']}"]
    probability = prediction.get("probability", {})
    if probability.get("status") == "EVALUATED":
        lines.append(
            "Mean probability: "
            f"reference={probability['reference_mean_probability']}, "
            f"current={probability['current_mean_probability']}"
        )
    decisions = prediction.get("decisions", {})
    if decisions.get("status") == "EVALUATED":
        lines.append(
            "Positive decision rate: "
            f"reference={decisions['reference_positive_decision_rate']}, "
            f"current={decisions['current_positive_decision_rate']}"
        )
    return "\n".join(lines) + "\n"


@dataclass
class _PreparedOutput:
    destination: Path
    temporary: Path
    backup: Path | None = None
    original_moved: bool = False
    published: bool = False


def _write_sibling_temp(destination: Path, content: str) -> Path:
    file_descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(raw_path)
    descriptor_open = True
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor_open = False
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if descriptor_open:
            os.close(file_descriptor)
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _unused_sibling_backup(destination: Path) -> Path:
    file_descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".bak", dir=destination.parent
    )
    os.close(file_descriptor)
    backup = Path(raw_path)
    backup.unlink()
    return backup


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        file_descriptor = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(file_descriptor)
    except OSError:
        pass
    finally:
        os.close(file_descriptor)


def _cleanup_publication_files(outputs: Sequence[_PreparedOutput]) -> list[OSError]:
    failures: list[OSError] = []
    for output in outputs:
        cleanup_paths: list[Path | None] = [output.temporary]
        if not output.original_moved:
            cleanup_paths.append(output.backup)
        for path in cleanup_paths:
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                failures.append(exc)
    return failures


def save_reports(
    report: Mapping[str, Any],
    json_path: Path,
    markdown_path: Path | None = None,
    *,
    overwrite: bool = False,
) -> tuple[Path, Path | None]:
    paths = [json_path]
    if markdown_path is not None:
        paths.append(markdown_path)
    if len({path.resolve() for path in paths}) != len(paths):
        raise MonitoringContractError("JSON and Markdown paths must differ")
    existing = [path for path in paths if path.exists()]
    if existing and not overwrite:
        raise MonitoringContractError(
            "refusing to overwrite existing output: " + ", ".join(map(str, existing))
        )
    serialized: list[tuple[Path, str]] = [
        (json_path, json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    ]
    if markdown_path is not None:
        serialized.append((markdown_path, render_markdown(report)))

    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)

    outputs: list[_PreparedOutput] = []
    try:
        for destination, content in serialized:
            outputs.append(
                _PreparedOutput(
                    destination=destination,
                    temporary=_write_sibling_temp(destination, content),
                )
            )

        for output in outputs:
            if overwrite and output.destination.exists():
                output.backup = _unused_sibling_backup(output.destination)
                os.replace(output.destination, output.backup)
                output.original_moved = True
            if overwrite:
                os.replace(output.temporary, output.destination)
                output.published = True
            else:
                os.link(output.temporary, output.destination)
                output.published = True
                output.temporary.unlink()
            _fsync_directory(output.destination.parent)
    except BaseException as publish_error:
        rollback_failures: list[OSError] = []
        for output in reversed(outputs):
            try:
                if output.original_moved and output.backup is not None:
                    os.replace(output.backup, output.destination)
                    output.original_moved = False
                    output.published = False
                elif output.published:
                    output.destination.unlink(missing_ok=True)
                    output.published = False
                _fsync_directory(output.destination.parent)
            except OSError as exc:
                rollback_failures.append(exc)
        rollback_failures.extend(_cleanup_publication_files(outputs))
        if rollback_failures:
            raise MonitoringContractError(
                "report publication failed and filesystem rollback was incomplete"
            ) from publish_error
        raise

    for output in outputs:
        output.original_moved = False
    cleanup_failures = _cleanup_publication_files(outputs)
    if cleanup_failures:
        raise MonitoringContractError("report publication succeeded but cleanup was incomplete")
    return json_path, markdown_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare current data with an authorized reference monitoring contract."
    )
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--deployment-state", type=Path)
    parser.add_argument("--reference-source", required=True)
    parser.add_argument("--current-source", required=True)
    parser.add_argument("--reference-window-start", required=True)
    parser.add_argument("--reference-window-end", required=True)
    parser.add_argument("--current-window-start", required=True)
    parser.add_argument("--current-window-end", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        contract = load_monitoring_contract(args.contract)
        if args.deployment_state is not None:
            contract = apply_deployment_state(contract, args.deployment_state)
        reference = pd.read_csv(args.reference)
        current = pd.read_csv(args.current)
        report = compute_drift_report(
            reference,
            current,
            contract=contract,
            reference_source=args.reference_source,
            current_source=args.current_source,
            reference_window=MonitoringWindow.from_values(
                args.reference_window_start, args.reference_window_end, "reference"
            ),
            current_window=MonitoringWindow.from_values(
                args.current_window_start, args.current_window_end, "current"
            ),
        )
        save_reports(report, args.output_json, args.output_markdown, overwrite=args.overwrite)
    except (
        MonitoringContractError,
        OSError,
        pd.errors.ParserError,
        pd.errors.EmptyDataError,
    ) as exc:
        print(f"drift monitoring failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return report_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
