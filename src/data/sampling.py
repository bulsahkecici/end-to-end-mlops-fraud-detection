"""Deterministic row sampling for large CSV ingestion.

Replaces the previous ``pd.read_csv(..., nrows=N)`` behaviour, which always
took the first N rows of the file (effectively a "head", not a sample — the
IEEE-CIS transaction file is time-ordered, so a head-only read silently
truncates the training set to the earliest transactions and drops the whole
back half of the timeline).

Two strategies are supported:

* ``time_ordered`` (default): systematic sampling evenly spaced across the
  full row range, so the observed time span is preserved even when only a
  fraction of rows are read.
* ``random``: uniform random sample without replacement, seeded for
  reproducibility. Intended as an explicit smoke-test / fallback option.

Sampling only ever touches row *indices* — the CSV is still read via
``pandas.read_csv(..., skiprows=...)`` so unselected rows are never
materialized in memory.
"""
from __future__ import annotations

import numpy as np


def count_data_rows(path) -> int:
    """Count data rows in a CSV file (excludes the header line)."""
    with open(path, "rb") as f:
        return sum(1 for _ in f) - 1


def compute_sample_row_indices(
    total_rows: int,
    sample_rows: int | None,
    strategy: str = "time_ordered",
    seed: int = 42,
) -> np.ndarray | None:
    """Return sorted 0-indexed data-row positions to keep, or None for "read all".

    Positions are relative to the data rows (header excluded), matching
    pandas' 0-indexed row numbering when used with ``skiprows``.
    """
    if sample_rows is None or sample_rows >= total_rows:
        return None
    if sample_rows <= 0:
        raise ValueError(f"sample_rows must be positive, got {sample_rows}")

    if strategy == "time_ordered":
        idx = np.linspace(0, total_rows - 1, num=sample_rows, dtype=np.int64)
        idx = np.unique(idx)
    elif strategy == "random":
        rng = np.random.default_rng(seed)
        idx = rng.choice(total_rows, size=sample_rows, replace=False)
        idx = np.sort(idx)
    else:
        raise ValueError(
            f"Unknown sampling_strategy={strategy!r}, expected 'time_ordered' or 'random'"
        )
    return idx


def skiprows_from_indices(total_rows: int, keep_indices: np.ndarray) -> set[int]:
    """Convert 0-indexed data-row positions to keep into a pandas ``skiprows`` set.

    pandas row 0 is the header (always kept); data row i corresponds to
    pandas file-row i + 1.
    """
    keep = set((keep_indices + 1).tolist())
    return {r for r in range(1, total_rows + 1) if r not in keep}


def optimize_dtypes(df):
    """Downcast numeric columns to smaller dtypes to reduce memory footprint."""
    import pandas as pd

    for col in df.columns:
        dtype = df[col].dtype
        if pd.api.types.is_float_dtype(dtype):
            df[col] = pd.to_numeric(df[col], downcast="float")
        elif pd.api.types.is_integer_dtype(dtype):
            df[col] = pd.to_numeric(df[col], downcast="integer")
    return df
