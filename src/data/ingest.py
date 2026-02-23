"""
IEEE-CIS Fraud Detection (Vesta) data ingest.
Expects CSV files from Kaggle: train_transaction.csv, train_identity.csv,
test_transaction.csv, test_identity.csv in DATA_DIR.
"""
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "processed" / "ieee-fraud-detection"

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
            f"{TRANSACTION_TRAIN} (and {IDENTITY_TRAIN}) into {base_dir}"
        )
    if not i.exists():
        raise FileNotFoundError(
            f"Identity file not found: {i}\n"
            f"Download the IEEE-CIS Fraud Detection dataset from Kaggle and place "
            f"{i} into {base_dir}"
        )
    return t, i


def load_train(
    data_dir: Path | str | None = None,
    sample_rows: int | None = 300_000,
) -> pd.DataFrame:
    """
    Load and merge train_transaction + train_identity on TransactionID (left join).
    Optionally limit transaction rows for memory (e.g. 300_000 for 8GB RAM).
    Identity is loaded fully so merge is correct.
    """
    base = Path(data_dir) if data_dir is not None else DATA_DIR
    t_path, i_path = _check_files(base, train=True)

    trans = pd.read_csv(t_path, nrows=sample_rows)
    identity = pd.read_csv(i_path)
    merged = trans.merge(identity, on="TransactionID", how="left")
    return merged


def load_test(
    data_dir: Path | str | None = None,
    sample_rows: int | None = 300_000,
) -> pd.DataFrame:
    """
    Load and merge test_transaction + test_identity on TransactionID (left join).
    Optionally limit transaction rows.
    """
    base = Path(data_dir) if data_dir is not None else DATA_DIR
    t_path, i_path = _check_files(base, train=False)

    trans = pd.read_csv(t_path, nrows=sample_rows)
    identity = pd.read_csv(i_path)
    merged = trans.merge(identity, on="TransactionID", how="left")
    return merged
