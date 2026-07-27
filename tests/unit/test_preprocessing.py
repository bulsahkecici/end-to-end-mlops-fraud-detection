from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.pipeline import (
    ALWAYS_DROP_COLS,
    UNKNOWN_CATEGORY_CODE,
    ColumnAligner,
    build_full_pipeline,
    build_preprocessor,
    infer_schema,
)


@pytest.fixture
def raw_train_df():
    return pd.DataFrame(
        {
            "TransactionID": [1, 2, 3, 4, 5, 6],
            "isFraud": [0, 1, 0, 0, 1, 0],
            "amount": [10.0, np.nan, 30.0, 40.0, 50.0, 60.0],
            "count": [1.0, 2.0, np.nan, 4.0, 5.0, 6.0],
            "category": ["a", "b", None, "a", "b", "c"],
            "high_card": [f"id_{i}" for i in range(6)],  # > cat_nunique_max when threshold small
        }
    )


def test_infer_schema_splits_numeric_and_categorical(raw_train_df):
    X = raw_train_df.drop(columns=["isFraud"])
    schema = infer_schema(X, cat_nunique_max=3)
    assert set(schema.numeric_cols) == {"amount", "count"}
    assert schema.categorical_cols == ["category"]
    assert "high_card" in schema.dropped_cols
    assert "TransactionID" not in schema.use_cols


def test_infer_schema_never_touches_target(raw_train_df):
    # Always-drop cols must never leak into the schema even if present.
    assert {"TransactionID", "isFraud"} == ALWAYS_DROP_COLS


def test_column_aligner_imputes_missing_numeric_and_categorical(raw_train_df):
    X = raw_train_df.drop(columns=["isFraud", "TransactionID"])
    schema = infer_schema(raw_train_df.drop(columns=["isFraud"]), cat_nunique_max=3)
    aligner = ColumnAligner(schema.numeric_cols, schema.categorical_cols).fit(X)
    preprocessor = build_preprocessor(schema)
    transformed = preprocessor.fit_transform(aligner.transform(X))
    assert not np.isnan(transformed).any()


def test_column_aligner_handles_missing_columns_at_inference(raw_train_df):
    X = raw_train_df.drop(columns=["isFraud", "TransactionID"])
    schema = infer_schema(raw_train_df.drop(columns=["isFraud"]), cat_nunique_max=3)
    aligner = ColumnAligner(schema.numeric_cols, schema.categorical_cols).fit(X)

    incoming = pd.DataFrame([{"amount": 15.0}])  # 'count' and 'category' entirely missing
    aligned = aligner.transform(incoming)
    assert list(aligned.columns) == schema.numeric_cols + schema.categorical_cols
    assert aligned.loc[0, "count"] != aligned.loc[0, "count"]  # NaN


def test_column_aligner_drops_extra_columns(raw_train_df):
    X = raw_train_df.drop(columns=["isFraud", "TransactionID"])
    schema = infer_schema(raw_train_df.drop(columns=["isFraud"]), cat_nunique_max=3)
    aligner = ColumnAligner(schema.numeric_cols, schema.categorical_cols).fit(X)

    incoming = pd.DataFrame([{"amount": 15.0, "unexpected_field": "nonsense", "another": 123}])
    aligned = aligner.transform(incoming)
    assert "unexpected_field" not in aligned.columns
    assert "another" not in aligned.columns


def test_column_aligner_reorders_columns(raw_train_df):
    X = raw_train_df.drop(columns=["isFraud", "TransactionID"])
    schema = infer_schema(raw_train_df.drop(columns=["isFraud"]), cat_nunique_max=3)
    aligner = ColumnAligner(schema.numeric_cols, schema.categorical_cols).fit(X)

    reordered = X[["category", "count", "amount"]]
    aligned = aligner.transform(reordered)
    assert list(aligned.columns) == schema.numeric_cols + schema.categorical_cols


def test_unknown_category_at_inference_does_not_crash(raw_train_df):
    X = raw_train_df.drop(columns=["isFraud", "TransactionID"])
    schema = infer_schema(raw_train_df.drop(columns=["isFraud"]), cat_nunique_max=3)
    aligner = ColumnAligner(schema.numeric_cols, schema.categorical_cols).fit(X)
    preprocessor = build_preprocessor(schema)
    preprocessor.fit(aligner.transform(X))

    incoming = pd.DataFrame([{"amount": 1.0, "count": 1.0, "category": "never_seen_before"}])
    transformed = preprocessor.transform(aligner.transform(incoming))
    cat_col_idx = schema.numeric_cols.__len__()  # 'cat' block starts right after numeric block
    assert transformed[0, cat_col_idx] == UNKNOWN_CATEGORY_CODE


def test_single_and_multi_record_consistency(raw_train_df):
    X = raw_train_df.drop(columns=["isFraud", "TransactionID"])
    schema = infer_schema(raw_train_df.drop(columns=["isFraud"]), cat_nunique_max=3)
    aligner = ColumnAligner(schema.numeric_cols, schema.categorical_cols).fit(X)
    preprocessor = build_preprocessor(schema)
    preprocessor.fit(aligner.transform(X))

    single = pd.DataFrame([{"amount": 42.0, "count": 3.0, "category": "a"}])
    batch = pd.DataFrame(
        [
            {"amount": 42.0, "count": 3.0, "category": "a"},
            {"amount": 5.0, "count": 1.0, "category": "b"},
        ]
    )
    single_t = preprocessor.transform(aligner.transform(single))
    batch_t = preprocessor.transform(aligner.transform(batch))
    np.testing.assert_allclose(single_t[0], batch_t[0])


def test_preprocessor_fit_only_uses_given_data_not_global_state(raw_train_df):
    """Fitting on a subset must not depend on rows outside that subset (no leakage)."""
    X = raw_train_df.drop(columns=["isFraud", "TransactionID"])
    schema = infer_schema(X, cat_nunique_max=3)

    train_subset = X.iloc[:3]
    aligner = ColumnAligner(schema.numeric_cols, schema.categorical_cols).fit(train_subset)
    preprocessor = build_preprocessor(schema)
    preprocessor.fit(aligner.transform(train_subset))
    median_from_subset = (
        preprocessor.named_transformers_["num"].named_steps["imputer"].statistics_[0]
    )

    # Median of the first 3 'amount' values (10.0, NaN, 30.0) -> median of [10, 30] = 20
    assert median_from_subset == pytest.approx(20.0)


def test_build_full_pipeline_end_to_end_with_classifier(raw_train_df):
    from sklearn.linear_model import LogisticRegression

    X = raw_train_df.drop(columns=["isFraud", "TransactionID"])
    y = raw_train_df["isFraud"]
    schema = infer_schema(X, cat_nunique_max=3)
    pipeline = build_full_pipeline(schema, LogisticRegression())
    pipeline.fit(X, y)
    proba = pipeline.predict_proba(X)[:, 1]
    assert ((proba >= 0) & (proba <= 1)).all()
