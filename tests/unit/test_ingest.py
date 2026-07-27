from __future__ import annotations

import pytest

from src.data.ingest import load_test, load_train, make_synthetic_transactions
from src.data.sampling import compute_sample_row_indices, count_data_rows
from src.data.validation import DataValidationError, validate_raw_transactions


def test_load_train_merges_transaction_and_identity(fixture_data_dir):
    df = load_train(data_dir=fixture_data_dir, sample_rows=None)
    assert len(df) == 40
    assert "id_01" in df.columns  # merged in from identity
    assert "DeviceType" in df.columns
    # left join: not every transaction has identity data -> some NaN id_01
    assert df["id_01"].isna().any()


def test_load_train_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_train(data_dir=tmp_path, sample_rows=None)


def test_load_test_has_no_target(fixture_data_dir):
    df = load_test(data_dir=fixture_data_dir, sample_rows=None)
    assert "isFraud" not in df.columns
    assert len(df) == 40


def test_sample_rows_is_deterministic(fixture_data_dir):
    df1 = load_train(data_dir=fixture_data_dir, sample_rows=20, sampling_strategy="time_ordered", seed=1)
    df2 = load_train(data_dir=fixture_data_dir, sample_rows=20, sampling_strategy="time_ordered", seed=1)
    assert len(df1) == 20
    pd_assert_frame_equal_ids(df1, df2)


def pd_assert_frame_equal_ids(df1, df2):
    assert list(df1["TransactionID"]) == list(df2["TransactionID"])


def test_sample_rows_preserves_time_span(fixture_data_dir):
    """time_ordered sampling should not just take the head of the file."""
    full = load_train(data_dir=fixture_data_dir, sample_rows=None)
    sampled = load_train(data_dir=fixture_data_dir, sample_rows=15, sampling_strategy="time_ordered", seed=0)
    assert sampled["TransactionDT"].max() > full["TransactionDT"].quantile(0.5)


def test_compute_sample_row_indices_bounds():
    idx = compute_sample_row_indices(total_rows=1000, sample_rows=100, strategy="time_ordered")
    assert idx is not None
    assert 90 <= len(idx) <= 100
    assert idx.min() >= 0 and idx.max() < 1000
    assert (idx == sorted(idx)).all()


def test_compute_sample_row_indices_none_when_covering_all():
    assert compute_sample_row_indices(total_rows=100, sample_rows=1000) is None


def test_count_data_rows(fixture_data_dir):
    assert count_data_rows(fixture_data_dir / "train_transaction.csv") == 40


def test_make_synthetic_transactions_shape_and_classes():
    df = make_synthetic_transactions(n=500, seed=42, fraud_rate=0.1)
    assert len(df) == 500
    assert set(df["isFraud"].unique()) <= {0, 1}
    assert df["isFraud"].sum() > 0
    assert df["TransactionAmt"].isna().sum() == 0
    assert df["D1"].isna().sum() > 0  # missingness injected deliberately


def test_make_synthetic_transactions_deterministic():
    df1 = make_synthetic_transactions(n=200, seed=7)
    df2 = make_synthetic_transactions(n=200, seed=7)
    assert df1.equals(df2)


def test_validate_raw_transactions_rejects_bad_target():
    df = make_synthetic_transactions(n=50, seed=1)
    df.loc[0, "isFraud"] = 2
    with pytest.raises(DataValidationError):
        validate_raw_transactions(df, require_target=True)


def test_validate_raw_transactions_rejects_missing_columns():
    df = make_synthetic_transactions(n=50, seed=1).drop(columns=["TransactionAmt"])
    with pytest.raises(DataValidationError):
        validate_raw_transactions(df, require_target=True)


def test_validate_raw_transactions_rejects_duplicate_ids():
    df = make_synthetic_transactions(n=50, seed=1)
    df.loc[1, "TransactionID"] = df.loc[0, "TransactionID"]
    with pytest.raises(DataValidationError):
        validate_raw_transactions(df, require_target=True)


def test_validate_raw_transactions_ok():
    df = make_synthetic_transactions(n=50, seed=1)
    validate_raw_transactions(df, require_target=True)  # should not raise
