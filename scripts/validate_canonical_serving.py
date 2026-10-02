"""Check the running canonical API with handmade, unrestricted request values."""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

import mlflow
import pandas as pd

from scripts.canonical_release import write_json
from scripts.verify_canonical_release import require


def validate(base: str, directory: Path) -> dict:
    training = json.loads((directory / "training_manifest.json").read_text())
    metadata = json.loads((directory / "model_metadata.json").read_text())
    version, run_id = training["model_version"], training["run_id"]
    name = training["model_name"]
    uri = f"models:/{name}/{version}"

    def call(path: str, payload: dict | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(
            base + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    status, health = call("/health")
    require(status == 200, "Liveness failed")
    status, ready = call("/ready")
    require(status == 200, "Readiness failed")
    require(ready["model_source"] == f"version:{version}", "Mutable/wrong model source")
    require(ready["model_version"] == version and ready["run_id"] == run_id, "Wrong model")
    require(ready["model_name"] == name, "Wrong model name")
    records = [{"TransactionAmt": 100.0}, {"TransactionAmt": 500.0, "ProductCD": "C"}]
    status, single = call("/predict", {"records": records[:1]})
    require(status == 200 and len(single["predictions"]) == 1, "Single prediction failed")
    status, batch = call("/predict", {"records": records})
    require(status == 200 and len(batch["predictions"]) == 2, "Batch prediction failed")
    wrapper = mlflow.pyfunc.load_model(uri).unwrap_python_model()
    expected = wrapper.predict(None, pd.DataFrame(records)).to_dict(orient="records")
    require(batch["predictions"] == expected, "Train/serve prediction divergence")
    for response in (single, batch):
        require(response["model_name"] == name, "Wrong prediction model name")
        require(response["model_version"] == version, "Wrong prediction version")
        require(
            all(p["threshold"] == metadata["threshold"] for p in response["predictions"]),
            "Threshold differs from stored threshold",
        )
    for payload in (
        {"records": []},
        {"records": [{}]},
        {"records": [{"TransactionAmt": True}]},
        {"records": [{"ProductCD": 5}]},
    ):
        status, _ = call("/predict", payload)
        require(status == 422, "Malformed request contract changed")
    return {
        "ready": ready,
        "health": health,
        "threshold": metadata["threshold"],
        "single_prediction": single,
        "batch_prediction": batch,
        "fixture_provenance": "handmade unrestricted values; no IEEE rows exported",
        "checks": {
            "health": True,
            "immutable_readiness": True,
            "single_prediction": True,
            "batch_prediction": True,
            "stored_threshold": True,
            "pipeline_parity": True,
            "malformed_requests_422": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8326")
    parser.add_argument("--directory", type=Path, default=Path("releases/ieee-cis-v1"))
    args = parser.parse_args()
    write_json(args.directory / "serving_manifest.json", validate(args.base_url, args.directory))
    print("Canonical immutable API serving checks passed")


if __name__ == "__main__":
    main()
