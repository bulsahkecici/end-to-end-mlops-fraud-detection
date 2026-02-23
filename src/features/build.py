"""
Baseline feature builder for IEEE-CIS Fraud Detection.
Drops TransactionID, keeps numeric + low-cardinality categorical, fills missing.
"""
from __future__ import annotations

import pandas as pd

TARGET_COL = "isFraud"
ID_COL = "TransactionID"
MAX_CAT_UNIQUE = 200
FILL_MISSING_CAT = "MISSING"


def build_features(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series | None, dict]:
    """
    Build baseline features from merged transaction+identity dataframe.
    - Drops TransactionID.
    - Numeric columns: kept, filled with median.
    - Categorical: kept only if nunique <= MAX_CAT_UNIQUE, converted to category, filled with FILL_MISSING_CAT.
    Returns (X, y, meta). y is None if TARGET_COL is not in df (e.g. test).
    """
    df = df.copy()
    if ID_COL in df.columns:
        df = df.drop(columns=[ID_COL])

    y = None
    if TARGET_COL in df.columns:
        y = df[TARGET_COL].copy()
        df = df.drop(columns=[TARGET_COL])

    num_cols = df.select_dtypes(include=["number"]).columns.tolist()
    obj_cols = df.select_dtypes(include=["object"]).columns.tolist()

    cat_cols = []
    for c in obj_cols:
        if df[c].nunique() <= MAX_CAT_UNIQUE:
            cat_cols.append(c)

    use_cols = num_cols + cat_cols
    X = df.loc[:, use_cols].copy()

    for c in num_cols:
        med = X[c].median()
        X.loc[:, c] = X[c].fillna(med)

    for c in cat_cols:
        X.loc[:, c] = X[c].astype("category")
        X.loc[:, c] = X[c].cat.add_categories(FILL_MISSING_CAT).fillna(FILL_MISSING_CAT)

    meta = {
        "use_cols": use_cols,
        "num_cols": num_cols,
        "cat_cols": cat_cols,
    }
    return X, y, meta
