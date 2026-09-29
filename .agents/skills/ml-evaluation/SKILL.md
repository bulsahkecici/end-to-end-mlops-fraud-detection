---
name: ml-evaluation
description: Design or review leakage-safe model evaluation for this repository, including temporal splits, canonical dataset identity, metrics, thresholds, and calibration.
---

# ML evaluation

- Preserve temporal semantics: train on the earliest window, validate on the following window, and reserve the latest final test window for one unbiased report. Random stratified splits are smoke-test-only and must be labeled as such.
- Infer schemas and fit preprocessing, encoders, imputers, feature selection, and model parameters on training data only.
- Use validation data for early stopping, model or hyperparameter selection, threshold selection, and calibration choice/fitting. Never use final test results to choose, promote, or revise a model.
- Compare models only from predictions on the same frozen canonical evaluation rows and labels. Record a stable evaluation dataset/version fingerprint plus source-file fingerprints, sampling configuration, split boundaries, and relevant row identity.
- Report PR-AUC, ROC-AUC, precision, recall, F1, Brier score, log loss, confusion counts, and expected business cost using documented false-negative and false-positive costs. When calibration is involved, add calibration diagnostics appropriate to the experiment and compare them on the same evaluation data.
- Keep threshold-dependent and threshold-independent metrics distinct, and record the evaluated threshold and strategy.
- Treat synthetic results only as pipeline/CI evidence. Never describe them as IEEE-CIS model quality.
- Preserve the baseline unless a comparable, reproducible, leakage-safe benchmark demonstrates improvement.
