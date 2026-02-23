import pandas as pd

TARGET_COL = "isFraud"
DROP_COLS_ALWAYS = {"TransactionID"}
CAT_NUNIQUE_MAX = 200
FILL_MISSING_CAT = "MISSING"

def build_features(df: pd.DataFrame, is_train: bool = True):
    df = df.copy()

    y = None
    if is_train:
        if TARGET_COL not in df.columns:
            raise ValueError(f"{TARGET_COL} column not found.")
        y = df[TARGET_COL].astype("int8")
        df = df.drop(columns=[TARGET_COL])

    # drop ids
    drop_cols = [c for c in DROP_COLS_ALWAYS if c in df.columns]
    if drop_cols:
        df = df.drop(columns=drop_cols)

    # numeric vs non-numeric
    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    non_num_cols = [c for c in df.columns if c not in num_cols]

    # keep only low-card categorical columns
    cat_cols = []
    for c in non_num_cols:
        nunique = df[c].nunique(dropna=True)
        if nunique <= CAT_NUNIQUE_MAX:
            cat_cols.append(c)

    use_cols = num_cols + cat_cols
    X = df[use_cols].copy()

    # numeric: float32 + median fill
    for c in num_cols:
        if c in X.columns:
            X.loc[:, c] = pd.to_numeric(X[c], errors="coerce").astype("float32")
            med = X[c].median()
            X.loc[:, c] = X[c].fillna(med)

    # categorical: force category + add missing + fill
    for c in cat_cols:
        if c in X.columns:
            # Work on a temporary categorical series so .cat is always safe.
            ser = X[c].astype("category")
            if FILL_MISSING_CAT not in ser.cat.categories:
                ser = ser.cat.add_categories([FILL_MISSING_CAT])
            ser = ser.fillna(FILL_MISSING_CAT)
            X[c] = ser

    # Build category mappings AFTER dtype is guaranteed
    cat_mappings = {}
    for c in cat_cols:
        if c in X.columns:
            # Safety: enforce again (cheap)
            if not pd.api.types.is_categorical_dtype(X[c]):
                X[c] = X[c].astype("category")
            cat_mappings[c] = X[c].cat.categories.tolist()

    meta = {
        "num_cols": num_cols,
        "cat_cols": cat_cols,
        "use_cols": use_cols,
        "cat_mappings": cat_mappings,
    }
    return X, y, meta
