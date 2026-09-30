"""Optional probability calibration for the canonical sklearn pipeline."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

VALID_CALIBRATION_STRATEGIES = {"none", "sigmoid", "isotonic"}
PROBABILITY_EPSILON = 1e-7
TARGET_COL = "isFraud"
TIME_COL = "TransactionDT"


def split_calibration_and_selection(
    selection_validation_pool: pd.DataFrame,
    strategy: str,
    seed: int,
    calibration_share: float = 0.5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reserve disjoint calibration-fit and model-selection rows."""
    if not 0.0 < calibration_share < 1.0:
        raise ValueError("calibration_share must be strictly between 0 and 1")
    if len(selection_validation_pool) < 2:
        raise ValueError("selection-validation pool needs at least two rows")
    if strategy == "temporal":
        ordered = selection_validation_pool.sort_values(TIME_COL, kind="mergesort")
        cut = max(1, min(len(ordered) - 1, int(round(len(ordered) * calibration_share))))
        return ordered.iloc[:cut].copy(), ordered.iloc[cut:].copy()
    if strategy == "random":
        target = selection_validation_pool[TARGET_COL]
        counts = target.value_counts()
        stratify = target if len(counts) > 1 and int(counts.min()) >= 2 else None
        calibration, selection = train_test_split(
            selection_validation_pool,
            train_size=calibration_share,
            random_state=seed,
            stratify=stratify,
        )
        return calibration.copy(), selection.copy()
    raise ValueError(f"Unknown split_strategy={strategy!r}, expected 'temporal' or 'random'")


class CalibratedBinaryClassifier(ClassifierMixin, BaseEstimator):
    """Wrap an already-fitted binary estimator with a fitted probability map.

    Only the calibrator is fitted by :meth:`fit`; the base estimator must
    already be trained. Keeping both objects in this final pipeline step makes
    calibrated inference serialize through the existing joblib/MLflow path.
    """

    def __init__(self, estimator, method: str = "sigmoid", random_state: int = 42):
        self.estimator = estimator
        self.method = method
        self.random_state = random_state

    def fit(self, X, y):
        if self.method not in {"sigmoid", "isotonic"}:
            raise ValueError("Calibration method must be 'sigmoid' or 'isotonic'")
        labels = np.asarray(y, dtype=int)
        if set(np.unique(labels)) != {0, 1}:
            raise ValueError("Calibration requires both binary target classes")

        raw_probability = np.asarray(self.estimator.predict_proba(X)[:, 1], dtype=float)
        if self.method == "sigmoid":
            self.calibrator_ = LogisticRegression(random_state=self.random_state)
            self.calibrator_.fit(self._logit(raw_probability).reshape(-1, 1), labels)
        else:
            self.calibrator_ = IsotonicRegression(out_of_bounds="clip")
            self.calibrator_.fit(raw_probability, labels)
        self.classes_ = np.asarray([0, 1])
        return self

    def predict_proba(self, X) -> np.ndarray:
        raw_probability = np.asarray(self.estimator.predict_proba(X)[:, 1], dtype=float)
        if self.method == "sigmoid":
            positive_probability = self.calibrator_.predict_proba(
                self._logit(raw_probability).reshape(-1, 1)
            )[:, 1]
        else:
            positive_probability = self.calibrator_.predict(raw_probability)
        positive_probability = np.clip(positive_probability, 0.0, 1.0)
        return np.column_stack((1.0 - positive_probability, positive_probability))

    def predict(self, X) -> np.ndarray:
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)

    @staticmethod
    def _logit(probability: np.ndarray) -> np.ndarray:
        clipped = np.clip(probability, PROBABILITY_EPSILON, 1.0 - PROBABILITY_EPSILON)
        return np.log(clipped / (1.0 - clipped))


def fit_probability_calibrator(estimator, X, y, strategy: str, seed: int):
    """Return the estimator unchanged or wrapped with a fitted calibrator."""
    if strategy not in VALID_CALIBRATION_STRATEGIES:
        raise ValueError(
            f"Unknown calibration_strategy={strategy!r}, "
            f"expected one of {sorted(VALID_CALIBRATION_STRATEGIES)}"
        )
    if strategy == "none":
        return estimator
    return CalibratedBinaryClassifier(estimator=estimator, method=strategy, random_state=seed).fit(
        X, y
    )
