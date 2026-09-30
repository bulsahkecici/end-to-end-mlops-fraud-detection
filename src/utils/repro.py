"""Reproducibility helpers: global seeding and run metadata capture."""

from __future__ import annotations

import hashlib
import os
import platform
import random
import subprocess
import sys
from pathlib import Path

import numpy as np


def set_global_seed(seed: int) -> None:
    """Seed every RNG this project touches (python, numpy). LightGBM/sklearn
    seeds are passed explicitly through model params rather than globally."""
    random.seed(seed)
    np.random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)


def get_git_info(project_root: Path) -> dict:
    def _run(cmd: list[str]) -> str | None:
        try:
            return (
                subprocess.check_output(cmd, cwd=project_root, stderr=subprocess.DEVNULL)
                .decode()
                .strip()
            )
        except Exception:
            return None

    status = _run(["git", "status", "--porcelain"])
    return {
        "commit": _run(["git", "rev-parse", "HEAD"]),
        "branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "dirty": bool(status) if status is not None else None,
    }


def get_env_info() -> dict:
    import lightgbm
    import mlflow
    import numpy
    import pandas
    import sklearn

    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "sklearn_version": sklearn.__version__,
        "lightgbm_version": lightgbm.__version__,
        "mlflow_version": mlflow.__version__,
        "pandas_version": pandas.__version__,
        "numpy_version": numpy.__version__,
    }


def fingerprint_file(path: Path | str) -> str | None:
    """Cheap fingerprint (name + size + mtime hash) — avoids hashing
    multi-hundred-MB files fully, while still detecting a swapped dataset."""
    p = Path(path)
    if not p.exists():
        return None
    stat = p.stat()
    raw = f"{p.name}:{stat.st_size}:{int(stat.st_mtime)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def fingerprint_file_contents(path: Path | str) -> str | None:
    """Return a reproducible SHA-256 of the complete file contents."""
    p = Path(path)
    if not p.exists():
        return None
    digest = hashlib.sha256()
    with p.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
