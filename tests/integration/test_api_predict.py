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


def test_predict_extra_and_reordered_columns(promoted_client):
    client, _ = promoted_client
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


def test_predict_malformed_json_returns_422(promoted_client):
    client, _ = promoted_client
    resp = client.post(
        "/predict", content=b"not-json", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 422


def test_predict_gracefully_coerces_unparseable_numeric_values(promoted_client):
    # A nested JSON object in a numeric field can't be parsed as a number;
    # the ColumnAligner coerces it to NaN (median-imputed downstream) rather
    # than crashing — this is intentional robustness, not an error case.
    client, _ = promoted_client
    resp = client.post(
        "/predict",
        json={"records": [{"TransactionAmt": {"nested": "object"}}]},
    )
    assert resp.status_code == 200
    pred = resp.json()["predictions"][0]
    assert 0.0 <= pred["fraud_probability"] <= 1.0


def test_predict_response_never_leaks_raw_traceback_on_internal_error(promoted_client, monkeypatch):
    # Force a genuine internal failure (not a bad-input case) and assert the
    # API still returns a clean, generic error instead of a raw traceback.
    client, result = promoted_client
    from src.api import dependencies

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated internal failure: should never reach the client")

    monkeypatch.setattr(dependencies.model_state.model, "predict", _boom)
    resp = client.post("/predict", json={"records": [{"TransactionAmt": 1.0}]})
    assert resp.status_code == 400
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
