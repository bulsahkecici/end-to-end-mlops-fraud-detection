"""Classification metrics for fraud-detection model evaluation."""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)


def compute_metrics(y_true: ArrayLike, y_proba: ArrayLike, threshold: float = 0.5) -> dict:
    """Compute the full metric suite for a set of predictions at a given threshold.

    ROC-AUC / PR-AUC / log-loss / Brier score are threshold-independent
    (computed on raw probabilities); precision/recall/F1/confusion matrix
    depend on the supplied threshold.
    """
    y_true = np.asarray(y_true)
    y_proba = np.asarray(y_proba)
    y_pred = (y_proba >= threshold).astype(int)

    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()

    has_both_classes = len(np.unique(y_true)) > 1
    metrics = {
        "roc_auc": float(roc_auc_score(y_true, y_proba)) if has_both_classes else float("nan"),
        "pr_auc": (
            float(average_precision_score(y_true, y_proba)) if has_both_classes else float("nan")
        ),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "log_loss": (
            float(log_loss(y_true, y_proba, labels=[0, 1])) if has_both_classes else float("nan")
        ),
        "brier_score": float(brier_score_loss(y_true, y_proba)),
        "fraud_rate": float(np.mean(y_true)) if len(y_true) else float("nan"),
        "threshold": float(threshold),
        "n_samples": int(len(y_true)),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }
    return metrics
