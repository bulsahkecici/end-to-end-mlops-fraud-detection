from __future__ import annotations

from src.data.ingest import make_synthetic_transactions
from src.monitoring.drift import compute_drift_report, render_markdown, save_reports


def test_no_drift_when_reference_equals_current():
    df = make_synthetic_transactions(n=1000, seed=1)
    report = compute_drift_report(df, df)
    assert report["overall_drift_detected"] is False
    assert report["n_columns_drifted"] == 0


def test_numeric_shift_is_detected():
    reference = make_synthetic_transactions(n=1000, seed=1)
    current = make_synthetic_transactions(n=500, seed=2)
    current["TransactionAmt"] = current["TransactionAmt"] * 5.0  # obvious shift

    report = compute_drift_report(reference, current)
    assert report["overall_drift_detected"] is True
    assert report["columns"]["TransactionAmt"]["drifted"] is True


def test_categorical_new_category_is_reported():
    reference = make_synthetic_transactions(n=500, seed=1)
    current = reference.copy()
    current["ProductCD"] = "NEVER_SEEN_BEFORE"

    report = compute_drift_report(reference, current)
    assert report["columns"]["ProductCD"]["drifted"] is True
    assert "NEVER_SEEN_BEFORE" in report["columns"]["ProductCD"]["new_categories"]


def test_prediction_drift_reports_fraud_rate_shift():
    reference = make_synthetic_transactions(n=2000, seed=1, fraud_rate=0.05)
    current = make_synthetic_transactions(n=2000, seed=2, fraud_rate=0.30)

    report = compute_drift_report(reference, current)
    assert report["prediction_drift"] is not None
    assert report["prediction_drift"]["fraud_rate_shift"] > 0.1


def test_render_markdown_contains_key_sections():
    df = make_synthetic_transactions(n=200, seed=1)
    report = compute_drift_report(df, df)
    md = render_markdown(report)
    assert "# Drift report" in md
    assert "Overall drift detected" in md


def test_save_reports_writes_files(monkeypatch, tmp_path):
    from src.config import settings

    monkeypatch.setattr(settings, "reports_dir", tmp_path / "reports")
    df = make_synthetic_transactions(n=200, seed=1)
    report = compute_drift_report(df, df)

    json_path, md_path = save_reports(report)
    assert json_path.exists()
    assert md_path.exists()
    assert "Drift report" in md_path.read_text()
