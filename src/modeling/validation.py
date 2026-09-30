"""Train/validation/test split strategies.

Default is a *temporal* split on ``TransactionDT``: the earliest period goes
to train, the middle period to validation, the latest period to test. This
matches how the model will actually be used in production (predicting on
future, unseen transactions) and avoids the optimistic bias a random split
would introduce for a fraud-detection problem with drifting behaviour over
time.

Random stratified splitting is kept only as an explicit fallback for quick
smoke tests where temporal ordering doesn't matter (e.g. tiny synthetic
data).
"""

from __future__ import annotations

import pandas as pd
from sklearn.model_selection import train_test_split

from src.data.validation import summarize_split

TIME_COL = "TransactionDT"


def temporal_split(
    df: pd.DataFrame,
    train_ratio: float,
    val_ratio: float,
    test_ratio: float,
    time_col: str = TIME_COL,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df_sorted = df.sort_values(time_col, kind="mergesort").reset_index(drop=True)
    n = len(df_sorted)
    n_train = int(round(n * train_ratio))
    n_val = int(round(n * val_ratio))
    train = df_sorted.iloc[:n_train]
    val = df_sorted.iloc[n_train : n_train + n_val]
    test = df_sorted.iloc[n_train + n_val :]
    return train, val, test


def random_split(
    df: pd.DataFrame,
    train_ratio: float,
    val_ratio: float,
    test_ratio: float,
    seed: int,
    stratify_col: str = "isFraud",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stratify = df[stratify_col] if stratify_col in df.columns else None
    train, temp = train_test_split(
        df, test_size=(val_ratio + test_ratio), random_state=seed, stratify=stratify
    )
    temp_stratify = temp[stratify_col] if stratify_col in temp.columns else None
    relative_test = test_ratio / (val_ratio + test_ratio)
    val, test = train_test_split(
        temp, test_size=relative_test, random_state=seed, stratify=temp_stratify
    )
    return train, val, test


def split_data(
    df: pd.DataFrame,
    strategy: str,
    train_ratio: float,
    val_ratio: float,
    test_ratio: float,
    seed: int,
    time_col: str = TIME_COL,
    summarize_final_test: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Split raw data into train/val/test according to ``strategy``.

    Returns (train, val, test) with original index preserved from the
    sorted/shuffled dataframe (not reset to 0..n), and logs a summary of
    each split via ``src.data.validation.summarize_split``.
    """
    if strategy == "temporal":
        train, val, test = temporal_split(df, train_ratio, val_ratio, test_ratio, time_col)
    elif strategy == "random":
        train, val, test = random_split(df, train_ratio, val_ratio, test_ratio, seed)
    else:
        raise ValueError(f"Unknown split_strategy={strategy!r}, expected 'temporal' or 'random'")

    final_test_summary = (
        summarize_split(test, "test")
        if summarize_final_test
        else {"name": "test", "n_rows": int(len(test)), "reserved": True}
    )
    summaries = {
        "train": summarize_split(train, "train"),
        "val": summarize_split(val, "val"),
        "test": final_test_summary,
    }
    return train, val, test, summaries
