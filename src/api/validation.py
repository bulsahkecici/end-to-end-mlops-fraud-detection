"""Semantic validation for records presented to the inference boundary.

Transport shape is owned by :mod:`src.api.schemas`.  This module answers the
separate question of whether each well-formed JSON record contains values the
loaded model can meaningfully score.  Feature roles come from the serialized
model artifact; they are never inferred from request data or copied into the
API.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


class ModelContractError(RuntimeError):
    """Raised when a loaded model lacks usable authoritative schema metadata."""


@dataclass(frozen=True)
class ValidationIssue:
    """One client-correctable semantic problem in a request record."""

    record_index: int
    code: str
    message: str
    field: str | None = None

    def as_dict(self) -> dict[str, int | str]:
        issue: dict[str, int | str] = {
            "record_index": self.record_index,
            "code": self.code,
            "message": self.message,
        }
        if self.field is not None:
            issue["field"] = self.field
        return issue


class SemanticValidationError(ValueError):
    """Raised when one or more records violate the inference contract."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        super().__init__("One or more records failed semantic validation")
        self.issues = issues


@dataclass(frozen=True)
class ModelFeatureContract:
    """Feature roles extracted from authoritative metadata in a model artifact."""

    numeric_fields: frozenset[str]
    categorical_fields: frozenset[str]

    @property
    def model_fields(self) -> frozenset[str]:
        return self.numeric_fields | self.categorical_fields

    @classmethod
    def from_model(cls, model: object) -> ModelFeatureContract:
        metadata = getattr(model, "metadata", None)
        if not isinstance(metadata, dict):
            raise ModelContractError("Loaded model has no metadata mapping")

        schema = metadata.get("feature_schema")
        if not isinstance(schema, dict):
            raise ModelContractError("Loaded model metadata has no feature_schema mapping")

        numeric_fields = _read_field_names(schema, "numeric_cols")
        categorical_fields = _read_field_names(schema, "categorical_cols")
        if not numeric_fields and not categorical_fields:
            raise ModelContractError("Loaded model feature schema contains no usable fields")
        overlap = numeric_fields & categorical_fields
        if overlap:
            raise ModelContractError("Loaded model feature schema assigns duplicate field roles")

        return cls(
            numeric_fields=frozenset(numeric_fields),
            categorical_fields=frozenset(categorical_fields),
        )


def _read_field_names(schema: dict[str, Any], key: str) -> set[str]:
    raw_names = schema.get(key)
    if not isinstance(raw_names, list) or any(
        not isinstance(name, str) or not name for name in raw_names
    ):
        raise ModelContractError(f"Loaded model feature schema has invalid {key}")
    return set(raw_names)


def validate_records(records: list[dict[str, Any]], contract: ModelFeatureContract) -> None:
    """Reject malformed or semantically empty records before model prediction.

    Missing optional model fields and explicit ``null`` values remain valid.
    Extra fields are ignored and do not make an otherwise empty record usable.
    Numeric strings are accepted only when they represent finite numbers, while
    categorical values must be non-blank strings.  The function validates only;
    it never transforms values before the canonical model pipeline sees them.
    """

    issues: list[ValidationIssue] = []
    for record_index, record in enumerate(records):
        record_has_usable_feature = False
        record_has_invalid_feature = False

        for field, value in record.items():
            if field not in contract.model_fields or value is None:
                continue

            if isinstance(value, dict | list):
                issues.append(
                    ValidationIssue(
                        record_index=record_index,
                        field=field,
                        code="non_scalar_feature",
                        message="Model feature values must be JSON scalars or null.",
                    )
                )
                record_has_invalid_feature = True
                continue

            if field in contract.numeric_fields:
                if isinstance(value, bool) or not _is_finite_number(value):
                    issues.append(
                        ValidationIssue(
                            record_index=record_index,
                            field=field,
                            code="invalid_numeric",
                            message=(
                                "Numeric model features must be finite numbers "
                                "or numeric strings."
                            ),
                        )
                    )
                    record_has_invalid_feature = True
                else:
                    record_has_usable_feature = True
                continue

            if not isinstance(value, str):
                issues.append(
                    ValidationIssue(
                        record_index=record_index,
                        field=field,
                        code="invalid_categorical",
                        message="Categorical model features must be strings or null.",
                    )
                )
                record_has_invalid_feature = True
            elif not value.strip():
                issues.append(
                    ValidationIssue(
                        record_index=record_index,
                        field=field,
                        code="invalid_categorical",
                        message="Categorical model features must be non-blank strings or null.",
                    )
                )
                record_has_invalid_feature = True
            else:
                record_has_usable_feature = True

        if not record_has_usable_feature and not record_has_invalid_feature:
            issues.append(
                ValidationIssue(
                    record_index=record_index,
                    code="no_usable_features",
                    message=(
                        "Record must contain at least one recognized model feature "
                        "with a non-missing value."
                    ),
                )
            )

    if issues:
        raise SemanticValidationError(issues)


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, str) and not value.strip():
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False
