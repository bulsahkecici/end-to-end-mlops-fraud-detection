"""Executed inside each exact final image; synthetic compatibility only."""

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace


def main():
    sys.path.insert(0, "/app")
    assert sys.version_info[:2] == (3, 11)
    assert metadata.version("mlflow") == "2.22.5"
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    for name in [
        "mlflow",
        "pyarrow",
        "pandas",
        "numpy",
        "sklearn",
        "scipy",
        "lightgbm",
        "ssl",
        "sqlite3",
        "ctypes",
        "curses",
        "readline",
        "uuid",
        "zoneinfo",
    ]:
        importlib.import_module(name)
    import curses
    import ssl
    import uuid
    from zoneinfo import ZoneInfo

    assert ssl.create_default_context().get_ca_certs()
    assert ZoneInfo("Europe/Istanbul")
    curses.setupterm(term="xterm")
    assert uuid.uuid4().version == 4
    assert uuid.uuid1().version == 1
    for executable in ["/usr/bin/infocmp", "/usr/bin/nsenter", "/usr/bin/mount", "/bin/mount"]:
        assert not Path(executable).exists()
    assert not Path("/bin/sh").exists()
    for name in ["setuptools", "wheel"]:
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
        else:
            raise AssertionError(name)

    import joblib
    import mlflow.pyfunc
    import pandas as pd
    from lightgbm import LGBMClassifier
    from sklearn.pipeline import Pipeline

    from src.modeling.mlflow_wrapper import FraudModelWrapper

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        frame = pd.DataFrame({"value": range(40)})
        model = Pipeline(
            [("model", LGBMClassifier(n_estimators=2, min_child_samples=2, verbosity=-1))]
        )
        model.fit(frame, [0, 1] * 20)
        joblib.dump(model, root / "pipeline.joblib")
        (root / "metadata.json").write_text(json.dumps({"threshold": 0.5}))
        wrapper = FraudModelWrapper()
        artifacts = {
            "pipeline": str(root / "pipeline.joblib"),
            "metadata": str(root / "metadata.json"),
        }
        wrapper.load_context(SimpleNamespace(artifacts=artifacts))
        mlflow.pyfunc.save_model(str(root / "model"), python_model=wrapper, artifacts=artifacts)
        actual = mlflow.pyfunc.load_model(str(root / "model")).predict(frame)
        pd.testing.assert_frame_equal(wrapper.predict(None, frame), actual)
        shared = os.environ.get("SMOKE_MODEL_DIR")
        if shared:
            shared_root = Path(shared)
            if not (shared_root / "model").exists():
                shutil.copytree(root / "model", shared_root / "model")
                actual.to_json(shared_root / "expected.json")
            cross = mlflow.pyfunc.load_model(str(shared_root / "model")).predict(frame)
            expected = pd.read_json(shared_root / "expected.json")
            pd.testing.assert_frame_equal(expected, cross, check_exact=False, rtol=1e-8)

        env = os.environ.copy()
        env["MLFLOW_TRACKING_URI"] = f"sqlite:///{root}/tracking.db"
        process = subprocess.Popen(
            [
                "mlflow",
                "server",
                "--host",
                "127.0.0.1",
                "--port",
                "5055",
                "--workers",
                "1",
                "--backend-store-uri",
                env["MLFLOW_TRACKING_URI"],
                "--artifacts-destination",
                str(root / "artifacts"),
            ],
            env=env,
        )
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("MLflow server exited")
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:5055/health", timeout=2
                    ) as response:
                        assert response.status == 200
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                raise RuntimeError("MLflow health timeout")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    print(
        "Python, native libraries, certificates, timezone, serialization, pyfunc "
        "and live MLflow PASS; synthetic only"
    )


if __name__ == "__main__":
    main()
