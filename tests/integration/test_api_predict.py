from __future__ import annotations

import mlflow
import pytest
from fastapi.testclient import TestClient

from src.config import settings
from src.modeling.train import run_training


@pytest.fixture
def promoted_client(patch_mlflow_uri):
    """Train a synthetic model, promote it to champion, and yield a TestClient
    whose FastAPI lifespan loads that model — end to end, no mocking."""
    result = run_training(
        data_source="synthetic",
        n_synthetic=700,
        seed=7,
        tracking_uri=patch_mlflow_uri,
        register=True,
    )
    mlflow.set_tracking_uri(patch_mlflow_uri)
    client_mlflow = mlflow.MlflowClient()
    client_mlflow.set_registered_model_alias(
        settings.model_name, settings.champion_alias, result["model_version"]
    )

    from src.api.app import app

    with TestClient(app) as client:
        yield client, result


def test_health_endpoint(promoted_client):
    client, _ = promoted_client
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_metrics_endpoint_exposes_prometheus_format(promoted_client):
    client, result = promoted_client
    client.post("/predict", json={"records": [{"TransactionAmt": 10.0}]})

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert "http_requests_total" in body
    assert "predictions_total" in body
    assert "model_loaded 1.0" in body
    assert f'model_version="{result["model_version"]}"' in body


def test_ready_endpoint_after_promotion(promoted_client):
    client, result = promoted_client
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["model_version"] == result["model_version"]


def test_predict_readme_style_single_field(promoted_client):
    client, _ = promoted_client
    resp = client.post("/predict", json={"records": [{"TransactionAmt": 100.0}]})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["predictions"]) == 1
    pred = body["predictions"][0]
    assert 0.0 <= pred["fraud_probability"] <= 1.0
    assert pred["fraud_prediction"] in (0, 1)
    assert body["model_name"] == settings.model_name


def test_predict_multi_record(promoted_client):
    client, _ = promoted_client
    resp = client.post(
        "/predict",
        json={"records": [{"TransactionAmt": 1.0}, {"TransactionAmt": 500.0, "ProductCD": "C"}]},
    )
    assert resp.status_code == 200
    assert len(resp.json()["predictions"]) == 2


def test_predict_full_unknown_category_extra_and_reordered_columns(promoted_client):
    client, _ = promoted_client
    full_resp = client.post(
        "/predict",
        json={
            "records": [
                {
                    "TransactionDT": 86_400,
                    "TransactionAmt": 249.0,
                    "ProductCD": "W",
                    "card4": "visa",
                    "card6": "credit",
                    "P_emaildomain": "gmail.com",
                    "M1": "T",
                    "C1": 2.0,
                    "C2": 1.0,
                    "D1": 3.0,
                    "D2": 2.0,
                    "V1": 0.25,
                    "V2": -0.75,
                }
            ]
        },
    )
    assert full_resp.status_code == 200

    resp = client.post(
        "/predict",
        json={
            "records": [
                {
                    "M1": "T",
                    "unexpected_field": "should be ignored",
                    "TransactionAmt": 42.0,
                    "ProductCD": "never_seen_category",
                }
            ]
        },
    )
    assert resp.status_code == 200
    pred = resp.json()["predictions"][0]
    assert 0.0 <= pred["fraud_probability"] <= 1.0


def test_predict_empty_records_returns_422(promoted_client):
    client, _ = promoted_client
    resp = client.post("/predict", json={"records": []})
    assert resp.status_code == 422

    for record in ({}, {"unexpected_field": "value"}, {"TransactionAmt": None}):
        resp = client.post("/predict", json={"records": [record]})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "semantic_validation_failed"
        assert detail["errors"][0]["code"] == "no_usable_features"


def test_predict_malformed_json_returns_422(promoted_client):
    client, _ = promoted_client
    resp = client.post(
        "/predict", content=b"not-json", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 422

    for payload in (
        {"records": {"TransactionAmt": 1.0}},
        {"records": ["not-an-object"]},
        {"records": [{"TransactionAmt": 1.0}], "unexpected": True},
    ):
        assert client.post("/predict", json=payload).status_code == 422


def test_predict_rejects_malformed_numeric_and_non_scalar_model_features(promoted_client):
    client, _ = promoted_client
    cases = [
        ({"TransactionAmt": "not-a-number"}, "invalid_numeric"),
        ({"TransactionAmt": {"nested": "object"}}, "non_scalar_feature"),
        ({"TransactionAmt": [1, 2]}, "non_scalar_feature"),
    ]
    for record, expected_code in cases:
        resp = client.post("/predict", json={"records": [record]})
        assert resp.status_code == 422
        error = resp.json()["detail"]["errors"][0]
        assert error["record_index"] == 0
        assert error["field"] == "TransactionAmt"
        assert error["code"] == expected_code

    non_finite = client.post(
        "/predict",
        content=b'{"records":[{"TransactionAmt":NaN}]}',
        headers={"Content-Type": "application/json"},
    )
    assert non_finite.status_code == 422
    assert non_finite.json()["detail"]["errors"][0]["code"] == "invalid_numeric"


def test_predict_enforces_batch_and_actual_body_size_boundaries(promoted_client, monkeypatch):
    client, _ = promoted_client
    too_many = [{"TransactionAmt": 1.0}] * (settings.api_max_batch_size + 1)
    assert client.post("/predict", json={"records": too_many}).status_code == 422

    encoded = b'{"records":[{"TransactionAmt":1.0}]}'
    monkeypatch.setattr(settings, "api_max_request_bytes", len(encoded))
    at_limit = client.post(
        "/predict", content=encoded, headers={"Content-Type": "application/json"}
    )
    assert at_limit.status_code == 200

    monkeypatch.setattr(settings, "api_max_request_bytes", len(encoded) - 1)
    over_limit = client.post(
        "/predict",
        content=encoded,
        headers={"Content-Type": "application/json", "Content-Length": "0"},
    )
    assert over_limit.status_code == 413
    assert over_limit.json() == {"detail": "Request body too large"}


def test_predict_response_never_leaks_raw_traceback_on_internal_error(promoted_client, monkeypatch):
    # Force a genuine internal failure (not a bad-input case) and assert the
    # API still returns a clean, generic error instead of a raw traceback.
    client, result = promoted_client
    from src.api import dependencies

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated internal failure: should never reach the client")

    monkeypatch.setattr(dependencies.model_state.model, "predict", _boom)
    resp = client.post("/predict", json={"records": [{"TransactionAmt": 1.0}]})
    assert resp.status_code == 500
    assert "Traceback" not in resp.text
    assert "simulated internal failure" not in resp.text


def test_predict_without_promoted_model_returns_503(patch_mlflow_uri):
    """A freshly configured registry with no champion alias yet must fail
    startup gracefully and report 503, not crash the app."""
    from src.api.app import app

    with TestClient(app) as client:
        resp = client.get("/ready")
        assert resp.status_code == 503
        resp = client.post("/predict", json={"records": [{"TransactionAmt": 1.0}]})
        assert resp.status_code == 503
