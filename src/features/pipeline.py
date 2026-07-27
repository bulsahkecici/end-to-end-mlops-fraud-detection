"""Single shared preprocessing + model pipeline used by both training and serving.

This replaces the previous ``src/features/build.py`` approach, where
training encoded categorical columns as native pandas ``category`` dtype
(consumed directly by LightGBM) while the FastAPI service re-implemented a
separate manual category -> integer mapping. The two encodings were never
guaranteed to agree, which is a training/serving parity bug.

Here, feature alignment, imputation and categorical encoding are all
``sklearn`` transformers composed into one :class:`~sklearn.pipeline.Pipeline`
together with the classifier. Because the whole pipeline (including the
custom :class:`ColumnAligner` step) is a single fitted object, it can be
pickled, logged to MLflow as one artifact, and produces byte-for-byte
identical transforms at train and inference time.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

TARGET_COL = "isFraud"
ALWAYS_DROP_COLS = {"TransactionID", "isFraud"}
MISSING_CATEGORY_TOKEN = "__missing__"
UNKNOWN_CATEGORY_CODE = -1


@dataclass(frozen=True)
class FeatureSchema:
    """Column roles decided once, from training data only.

    ``cat_nunique_max`` bounds cardinality of columns kept as categorical
    (very-high-cardinality free-text-like columns such as ``DeviceInfo`` are
    dropped rather than blowing up the encoder). This decision only ever
    looks at column dtypes / cardinality on the training split — never at
    the label — so it does not leak validation/test information.
    """

    numeric_cols: list[str]
    categorical_cols: list[str]
    dropped_cols: list[str] = field(default_factory=list)

    @property
    def use_cols(self) -> list[str]:
        return self.numeric_cols + self.categorical_cols


def infer_schema(df: pd.DataFrame, cat_nunique_max: int = 200) -> FeatureSchema:
    """Infer numeric/categorical column roles from a (training) dataframe.

    Must only ever be called on the training split — calling it on
    validation/test data would let split-specific column statistics leak
    into the feature schema.
    """
    candidate_cols = [c for c in df.columns if c not in ALWAYS_DROP_COLS]

    numeric_cols: list[str] = []
    categorical_cols: list[str] = []
    dropped_cols: list[str] = []

    for col in candidate_cols:
        if pd.api.types.is_numeric_dtype(df[col]):
            numeric_cols.append(col)
        else:
            nunique = df[col].nunique(dropna=True)
            if nunique <= cat_nunique_max:
                categorical_cols.append(col)
            else:
                dropped_cols.append(col)

    return FeatureSchema(
        numeric_cols=numeric_cols, categorical_cols=categorical_cols, dropped_cols=dropped_cols
    )


class ColumnAligner(BaseEstimator, TransformerMixin):
    """Reindex an incoming dataframe onto the exact fitted feature schema.

    Handles, deterministically and without raising:

    * missing columns (added as all-NaN),
    * extra/unexpected columns (dropped),
    * reordered columns (reindexed back to fit-time order),
    * numeric columns arriving as strings/objects (coerced, unparsable -> NaN),
    * categorical columns arriving as non-string types (coerced to object).

    This is the first step of the pipeline, so every downstream step
    (imputers, encoder, classifier) always sees a stable, predictable shape.
    """

    def __init__(self, numeric_cols: list[str] | None = None, categorical_cols: list[str] | None = None):
        self.numeric_cols = numeric_cols
        self.categorical_cols = categorical_cols

    def fit(self, X: pd.DataFrame, y=None) -> "ColumnAligner":
        self.numeric_cols_ = list(self.numeric_cols or [])
        self.categorical_cols_ = list(self.categorical_cols or [])
        self.feature_names_in_ = self.numeric_cols_ + self.categorical_cols_
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(X, pd.DataFrame):
            X = pd.DataFrame(X)
        out = X.reindex(columns=self.feature_names_in_)
        for col in self.numeric_cols_:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
        for col in self.categorical_cols_:
            out[col] = out[col].astype(object)
            out[col] = out[col].where(out[col].notna(), None)
        return out

    def get_feature_names_out(self, input_features=None) -> np.ndarray:
        return np.array(self.feature_names_in_)


def build_preprocessor(schema: FeatureSchema) -> ColumnTransformer:
    """Build the ``ColumnTransformer`` that imputes and encodes aligned features."""
    numeric_pipeline = Pipeline(steps=[("imputer", SimpleImputer(strategy="median"))])
    categorical_pipeline = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="constant", fill_value=MISSING_CATEGORY_TOKEN)),
            (
                "encoder",
                OrdinalEncoder(
                    handle_unknown="use_encoded_value",
                    unknown_value=UNKNOWN_CATEGORY_CODE,
                    dtype=np.float64,
                ),
            ),
        ]
    )
    return ColumnTransformer(
        transformers=[
            ("num", numeric_pipeline, schema.numeric_cols),
            ("cat", categorical_pipeline, schema.categorical_cols),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )


def build_full_pipeline(schema: FeatureSchema, classifier) -> Pipeline:
    """Assemble the aligner + preprocessor + classifier into one Pipeline.

    The returned pipeline is unfitted. Callers that need early-stopping
    during LightGBM training should instead fit the aligner/preprocessor
    steps manually and construct the final ``Pipeline`` from already-fitted
    objects (see ``src/modeling/train.py``); ``Pipeline.predict`` works the
    same either way since it simply chains each step's ``transform``/
    ``predict`` in order.
    """
    aligner = ColumnAligner(numeric_cols=schema.numeric_cols, categorical_cols=schema.categorical_cols)
    preprocessor = build_preprocessor(schema)
    return Pipeline(
        steps=[
            ("aligner", aligner),
            ("preprocessor", preprocessor),
            ("classifier", classifier),
        ]
    )
