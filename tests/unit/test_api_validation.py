from __future__ import annotations

import pytest

from src.api.validation import (
    ModelContractError,
    ModelFeatureContract,
    SemanticValidationError,
    validate_records,
)


@pytest.fixture
def contract() -> ModelFeatureContract:
    return ModelFeatureContract(
        numeric_fields=frozenset({"TransactionAmt", "C1"}),
        categorical_fields=frozenset({"ProductCD"}),
    )


def test_contract_is_loaded_from_model_metadata():
    model = type(
        "Model",
        (),
        {
            "metadata": {
                "feature_schema": {
                    "numeric_cols": ["TransactionAmt"],
                    "categorical_cols": ["ProductCD"],
                }
            }
        },
    )()

    contract = ModelFeatureContract.from_model(model)

    assert contract.numeric_fields == {"TransactionAmt"}
    assert contract.categorical_fields == {"ProductCD"}


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"feature_schema": {}},
        {"feature_schema": {"numeric_cols": "TransactionAmt", "categorical_cols": []}},
        {
            "feature_schema": {
                "numeric_cols": ["TransactionAmt"],
                "categorical_cols": ["TransactionAmt"],
            }
        },
    ],
)
def test_contract_rejects_missing_or_invalid_model_metadata(metadata):
    model = type("Model", (), {"metadata": metadata})()

    with pytest.raises(ModelContractError):
        ModelFeatureContract.from_model(model)


def test_semantic_validation_accepts_supported_partial_inputs(contract):
    validate_records(
        [
            {"TransactionAmt": 100.0},
            {"TransactionAmt": "100.25"},
            {"ProductCD": "never-seen-category"},
            {"C1": None, "ProductCD": "W"},
            {"TransactionAmt": 5, "ignored_extra": {"nested": "is harmless"}},
        ],
        contract,
    )


@pytest.mark.parametrize(
    ("record", "code", "field"),
    [
        ({}, "no_usable_features", None),
        ({"ignored_extra": "value"}, "no_usable_features", None),
        ({"TransactionAmt": None}, "no_usable_features", None),
        ({"ProductCD": "  "}, "invalid_categorical", "ProductCD"),
        ({"TransactionAmt": "not-a-number"}, "invalid_numeric", "TransactionAmt"),
        ({"TransactionAmt": True}, "invalid_numeric", "TransactionAmt"),
        ({"TransactionAmt": float("nan")}, "invalid_numeric", "TransactionAmt"),
        ({"TransactionAmt": float("inf")}, "invalid_numeric", "TransactionAmt"),
        ({"TransactionAmt": {"nested": 1}}, "non_scalar_feature", "TransactionAmt"),
        ({"TransactionAmt": [1, 2]}, "non_scalar_feature", "TransactionAmt"),
        ({"ProductCD": 7}, "invalid_categorical", "ProductCD"),
    ],
)
def test_semantic_validation_rejects_unusable_or_malformed_features(contract, record, code, field):
    with pytest.raises(SemanticValidationError) as exc_info:
        validate_records([record], contract)

    issue = exc_info.value.issues[0]
    assert issue.record_index == 0
    assert issue.code == code
    assert issue.field == field


def test_semantic_validation_reports_invalid_batch_record_index(contract):
    with pytest.raises(SemanticValidationError) as exc_info:
        validate_records(
            [{"TransactionAmt": 10.0}, {"TransactionAmt": []}, {"ProductCD": "C"}],
            contract,
        )

    assert exc_info.value.issues[0].record_index == 1
