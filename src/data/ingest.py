"""IEEE-CIS Fraud Detection (Vesta) data ingest.

Expects CSV files from Kaggle: train_transaction.csv, train_identity.csv,
test_transaction.csv, test_identity.csv in ``settings.ieee_data_dir``.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.config import settings
from src.data.sampling import (
    compute_sample_row_indices,
    count_data_rows,
    optimize_dtypes,
    skiprows_from_indices,
)
from src.data.validation import validate_raw_transactions

TRANSACTION_TRAIN = "train_transaction.csv"
IDENTITY_TRAIN = "train_identity.csv"
TRANSACTION_TEST = "test_transaction.csv"
IDENTITY_TEST = "test_identity.csv"


def _check_files(base_dir: Path, train: bool) -> tuple[Path, Path]:
    if train:
        t, i = base_dir / TRANSACTION_TRAIN, base_dir / IDENTITY_TRAIN
    else:
        t, i = base_dir / TRANSACTION_TEST, base_dir / IDENTITY_TEST
    if not t.exists():
        raise FileNotFoundError(
            f"Transaction file not found: {t}\n"
            f"Download the IEEE-CIS Fraud Detection dataset from Kaggle and place "
            f"{t.name} (and {i.name}) into {base_dir}"
        )
    if not i.exists():
        raise FileNotFoundError(
            f"Identity file not found: {i}\n"
            f"Download the IEEE-CIS Fraud Detection dataset from Kaggle and place "
            f"{i.name} into {base_dir}"
        )
    return t, i


def _read_transaction_csv(
    path: Path,
    sample_rows: int | None,
    sampling_strategy: str,
    seed: int,
) -> pd.DataFrame:
    if sample_rows is None:
        df = pd.read_csv(path)
    else:
        total_rows = count_data_rows(path)
        keep_idx = compute_sample_row_indices(total_rows, sample_rows, sampling_strategy, seed)
        if keep_idx is None:
            df = pd.read_csv(path)
        else:
            skip = skiprows_from_indices(total_rows, keep_idx)
            df = pd.read_csv(path, skiprows=lambda r: r in skip)
    return optimize_dtypes(df)


def _load(
    data_dir: Path | str | None,
    train: bool,
    sample_rows: int | None,
    sampling_strategy: str,
    seed: int,
) -> pd.DataFrame:
    base = Path(data_dir) if data_dir is not None else settings.ieee_data_dir
    t_path, i_path = _check_files(base, train=train)

    trans = _read_transaction_csv(t_path, sample_rows, sampling_strategy, seed)
    identity = pd.read_csv(i_path)
    merged = trans.merge(identity, on="TransactionID", how="left")
    validate_raw_transactions(merged, require_target=train)
    return merged


def load_train(
    data_dir: Path | str | None = None,
    sample_rows: int | None = None,
    sampling_strategy: str | None = None,
    seed: int | None = None,
) -> pd.DataFrame:
    """Load and merge train_transaction + train_identity on TransactionID (left join).

    ``sample_rows`` limits the number of transaction rows read, using a
    deterministic sampling strategy (see :mod:`src.data.sampling`) rather
    than a naive head-of-file read. Identity is always loaded fully so the
    merge is correct.
    """
    return _load(
        data_dir,
        train=True,
        sample_rows=sample_rows if sample_rows is not None else settings.sample_rows,
        sampling_strategy=sampling_strategy or settings.sampling_strategy,
        seed=seed if seed is not None else settings.random_seed,
    )


def load_test(
    data_dir: Path | str | None = None,
    sample_rows: int | None = None,
    sampling_strategy: str | None = None,
    seed: int | None = None,
) -> pd.DataFrame:
    """Load and merge test_transaction + test_identity on TransactionID (left join)."""
    return _load(
        data_dir,
        train=False,
        sample_rows=sample_rows if sample_rows is not None else settings.sample_rows,
        sampling_strategy=sampling_strategy or settings.sampling_strategy,
        seed=seed if seed is not None else settings.random_seed,
    )


# Columns used by ``make_synthetic_transactions`` to mirror the real IEEE-CIS
# schema closely enough to exercise numeric + categorical + missing-value
# handling in tests and CI without needing the real (1.3GB) dataset.
SYNTHETIC_NUMERIC_COLS = ["TransactionAmt", "C1", "C2", "D1", "D2", "V1", "V2"]
SYNTHETIC_CATEGORICAL_COLS = ["ProductCD", "card4", "card6", "P_emaildomain", "M1"]


def make_synthetic_transactions(n: int = 2000, seed: int = 42, fraud_rate: float = 0.05) -> pd.DataFrame:
    """Generate a small synthetic dataset shaped like IEEE-CIS transactions.

    Used for unit tests, CI, and the ``train-smoke`` command so the pipeline
    can be exercised end-to-end without the real (multi-GB, credentialed)
    Kaggle dataset.
    """
    rng = np.random.default_rng(seed)
    product_codes = np.array(["W", "C", "R", "H", "S"])
    card4_vals = np.array(["visa", "mastercard", "discover", "american express"])
    card6_vals = np.array(["debit", "credit"])
    email_domains = np.array(["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", None])
    m1_vals = np.array(["T", "F", None])

    df = pd.DataFrame(
        {
            "TransactionID": np.arange(1, n + 1),
            "isFraud": rng.binomial(1, fraud_rate, size=n).astype("int64"),
            "TransactionDT": np.sort(rng.integers(86_400, 86_400 * 200, size=n)),
            "TransactionAmt": rng.gamma(2.0, 50.0, size=n).round(2),
            "ProductCD": rng.choice(product_codes, size=n),
            "card4": rng.choice(card4_vals, size=n),
            "card6": rng.choice(card6_vals, size=n),
            "P_emaildomain": rng.choice(email_domains, size=n),
            "M1": rng.choice(m1_vals, size=n),
            "C1": rng.poisson(3, size=n).astype("float64"),
            "C2": rng.poisson(2, size=n).astype("float64"),
            "D1": rng.exponential(10, size=n).round(1),
            "D2": rng.exponential(5, size=n).round(1),
            "V1": rng.normal(size=n).round(3),
            "V2": rng.normal(size=n).round(3),
        }
    )

    # Inject realistic missingness so imputation is actually exercised.
    for col in ["D1", "D2", "V1", "V2", "C2"]:
        mask = rng.random(n) < 0.1
        df.loc[mask, col] = np.nan

    return df
