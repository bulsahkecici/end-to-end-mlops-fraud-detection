from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.schemas import PredictionItem, PredictRequest, PredictResponse
from src.config import settings


def test_predict_request_rejects_empty_records():
    with pytest.raises(ValidationError):
        PredictRequest(records=[])


def test_predict_request_accepts_single_record():
    req = PredictRequest(records=[{"TransactionAmt": 100.0}])
    assert len(req.records) == 1


def test_predict_request_accepts_multiple_records():
    req = PredictRequest(records=[{"TransactionAmt": 1.0}, {"TransactionAmt": 2.0}])
    assert len(req.records) == 2


def test_predict_request_rejects_over_max_batch_size():
    too_many = [{"TransactionAmt": 1.0}] * (settings.api_max_batch_size + 1)
    with pytest.raises(ValidationError):
        PredictRequest(records=too_many)


@pytest.mark.parametrize(
    "payload",
    [
        {"records": {"TransactionAmt": 1.0}},
        {"records": [1.0]},
        {"records": [{"TransactionAmt": 1.0}], "unexpected": True},
    ],
)
def test_predict_request_rejects_malformed_transport_shape(payload):
    with pytest.raises(ValidationError):
        PredictRequest.model_validate(payload)


def test_predict_request_allows_arbitrary_extra_fields_per_record():
    req = PredictRequest(records=[{"TransactionAmt": 1.0, "some_unknown_field": "x", "n": None}])
    assert req.records[0]["some_unknown_field"] == "x"


def test_prediction_item_shape():
    item = PredictionItem(fraud_probability=0.87, fraud_prediction=1, threshold=0.5)
    assert item.fraud_probability == 0.87
    assert item.fraud_prediction == 1


def test_predict_response_shape():
    resp = PredictResponse(
        predictions=[PredictionItem(fraud_probability=0.1, fraud_prediction=0, threshold=0.5)],
        model_name="ieee_fraud_lgbm",
        model_version="3",
    )
    dumped = resp.model_dump()
    assert dumped["model_name"] == "ieee_fraud_lgbm"
    assert dumped["predictions"][0]["fraud_prediction"] == 0
