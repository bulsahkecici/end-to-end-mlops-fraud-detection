"""Decision-threshold selection strategies and cost-based evaluation.

Supported strategies:

* ``fixed``: use a fixed, pre-specified threshold (e.g. 0.5).
* ``best_f1``: maximize F1 on the validation set.
* ``target_recall``: maximize precision subject to recall >= a target value.
* ``cost_based``: minimize expected business cost given configurable
  false-negative / false-positive costs.

Thresholds must always be selected on the validation set; the test set is
reserved for a single final, unbiased evaluation.
"""
from __future__ import annotations

import numpy as np
from sklearn.metrics import confusion_matrix, precision_recall_curve

VALID_STRATEGIES = {"fixed", "best_f1", "target_recall", "cost_based"}


def expected_cost(y_true: np.ndarray, y_pred: np.ndarray, fn_cost: float, fp_cost: float) -> float:
    """Total expected cost = (#false negatives * fn_cost) + (#false positives * fp_cost)."""
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return float(fn * fn_cost + fp * fp_cost)


def find_best_f1_threshold(y_true: np.ndarray, y_proba: np.ndarray) -> float:
    precisions, recalls, thresholds = precision_recall_curve(y_true, y_proba)
    if len(thresholds) == 0:
        return 0.5
    f1s = 2 * precisions * recalls / np.clip(precisions + recalls, 1e-12, None)
    best_idx = int(np.argmax(f1s[:-1]))
    return float(thresholds[best_idx])


def find_target_recall_threshold(y_true: np.ndarray, y_proba: np.ndarray, target_recall: float) -> float:
    """Highest threshold that still achieves recall >= target_recall (maximizes precision)."""
    precisions, recalls, thresholds = precision_recall_curve(y_true, y_proba)
    if len(thresholds) == 0:
        return 0.5
    valid = recalls[:-1] >= target_recall
    if not valid.any():
        # Target recall unreachable even at the lowest threshold -> flag everything.
        return 0.0
    valid_idx = np.where(valid)[0]
    return float(thresholds[valid_idx[-1]])


def find_cost_based_threshold(
    y_true: np.ndarray, y_proba: np.ndarray, fn_cost: float, fp_cost: float
) -> tuple[float, float]:
    candidates = np.unique(np.clip(y_proba, 0.0, 1.0))
    if len(candidates) == 0:
        return 0.5, float("inf")
    if len(candidates) > 200:
        candidates = np.quantile(candidates, np.linspace(0, 1, 200))
    best_threshold, best_cost = 0.5, float("inf")
    for t in candidates:
        y_pred = (y_proba >= t).astype(int)
        cost = expected_cost(y_true, y_pred, fn_cost, fp_cost)
        if cost < best_cost:
            best_cost, best_threshold = cost, float(t)
    return best_threshold, best_cost


def select_threshold(
    y_true: np.ndarray,
    y_proba: np.ndarray,
    strategy: str,
    fixed_threshold: float = 0.5,
    target_recall: float = 0.80,
    fn_cost: float = 25.0,
    fp_cost: float = 1.0,
) -> dict:
    """Select a decision threshold on validation data and return it with context."""
    if strategy not in VALID_STRATEGIES:
        raise ValueError(f"Unknown threshold_strategy={strategy!r}, expected one of {VALID_STRATEGIES}")

    if strategy == "fixed":
        threshold = float(fixed_threshold)
    elif strategy == "best_f1":
        threshold = find_best_f1_threshold(y_true, y_proba)
    elif strategy == "target_recall":
        threshold = find_target_recall_threshold(y_true, y_proba, target_recall)
    else:  # cost_based
        threshold, _ = find_cost_based_threshold(y_true, y_proba, fn_cost, fp_cost)

    y_pred = (y_proba >= threshold).astype(int)
    cost = expected_cost(y_true, y_pred, fn_cost, fp_cost)
    return {
        "strategy": strategy,
        "threshold": float(threshold),
        "expected_cost": cost,
        "false_negative_cost": fn_cost,
        "false_positive_cost": fp_cost,
    }
