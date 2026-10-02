# Model card: canonical IEEE-CIS v1

## Purpose and intended use

Prioritize anonymized e-commerce transactions for fraud review. Probability and thresholded decisions support operator review; they are not a legal determination or sufficient grounds to deny payment automatically. This is a reproducible portfolio release, not a validated live banking service or an SLA-backed cloud deployment.

## REAL IEEE-CIS EVIDENCE

The permitted local Kaggle IEEE-CIS/Vesta files contain 590,540 labelled training transactions and 144,233 training identity records. Identity joins are left joins on unique `TransactionID`. All labelled transactions were used across training and reserved evaluation partitions. The competition's 506,691 unlabelled test transactions and 141,907 test identity records were structurally audited and hashed, but never scored for this release. Restricted rows, input examples, model binaries and frozen promotion Parquet remain in ignored local artifacts; none are redistributed.

Release `ieee-cis-v1`; source dataset fingerprint `a01f77eaa792c8346a876102fa3c717f7361ccd4e14fe1a57735b86816c7ab75`. Model `ieee_fraud_lgbm`, immutable version `1`, run `a7d516e70f714caeb67f35bd2c30762f`. Candidate → first approved champion → explicit deployment all resolve to that version in an isolated registry. Version numbers are local to that registry; reproductions produce new run/deployment IDs.

| Final-test metric | Value |
|---|---:|
| pr_auc | 0.5229777963628136 |
| roc_auc | 0.8956263566933376 |
| precision | 0.4429896344789962 |
| recall | 0.5267596496918586 |
| f1 | 0.4812564824418432 |
| log_loss | 0.09384780195084294 |
| brier_score | 0.02256234669894403 |
| threshold | 0.1508937436017237 |
| fraud_rate | 0.03480430340592226 |
| n_samples | 88581 |
| expected_cost | 38517.0 |
| expected_cost_per_sample | 0.434822365970129 |


Confusion counts: TN 83456, FP 2042, FN 1459, TP 1624. Cost uses illustrative FN=25 and FP=1 units, not measured financial loss. PR-AUC is sklearn average precision. These results describe one frozen baseline on a future temporal holdout; no alternative model was selected after viewing them.

## Temporal evaluation and leakage controls

Stable ascending `TransactionDT` order (`mergesort`) assigns earliest 70% to training, next 15% to development, latest 15% to final reporting. Development is divided into an earlier selection pool and later promotion pool; the earlier half of selection is for early stopping and optional calibration fitting, the later half for threshold selection. Exact boundaries and full-row fingerprints are in [split_manifest.json](../releases/ieee-cis-v1/split_manifest.json).

| Partition | Rows | TransactionDT inclusive range | Purpose |
|---|---:|---|---|
| Train | 413378 | 86400–10437996 | Schema, preprocessing, estimator fit |
| Calibration fit | 22145 | 10438003–11027920 | Early stopping; calibration disabled |
| Selection validation | 22145 | 11027943–11725688 | `best_f1` threshold selection |
| Promotion evaluation | 44291 | 11725712–13151840 | Absolute quality gates and comparable rescoring |
| Final test | 88581 | 13151880–15811131 | Single explicit release report |

Preprocessing and schema inference fit on train only. Early stopping uses AUC with patience 20. Final test does not participate in fitting, calibration, threshold selection, model selection, or promotion. Configuration was committed as `81cfdea4ccf98305e7e1aa5a6105c8cddd10996d` before training, and deployment/review frozen as `c54bf3380df626026d6921be93f9667bbb2f1374` before final reporting. Fingerprinting final rows before reporting did not generate predictions or metrics.

## Model, features, calibration and threshold

The shared fitted sklearn pipeline is `ColumnAligner` → `ColumnTransformer` → LightGBM. Train-inferred features include 401 numeric and 30 bounded categorical columns; `TransactionID` is excluded and high-cardinality `DeviceInfo` is dropped. Numeric median and categorical most-frequent imputation are train-fitted. The preserved ordinal/drop strategy ordinal-encodes categoricals, drops higher-cardinality fields when present, and handles unknown categories. LightGBM uses 500 maximum estimators, 31 leaves, learning rate 0.05, feature fraction 0.9, subsample 0.8 every 5 iterations, seed 42; exact parameters are preregistered.

Calibration is **none**. Scores are uncalibrated model probabilities, not asserted calibrated risks. Frequency encoding and sigmoid/isotonic exist as optional architecture variants and were not used. The threshold `0.1508937436017237` maximizes F1 only on selection validation. Business costs are reported but do not choose this threshold.

## Promotion, deployment and serving

Promotion verifies loadability, signature, probability validity, artifact byte checksum, canonical semantic and exact-row identity, candidate alias stability, and rescored PR-AUC ≥0.10 / recall ≥0.10. Existing champions must be rescored on the same frozen rows at their own stored thresholds and satisfy the regression gate. This isolated first real champion had no previous champion; no synthetic comparison was fabricated.

Promotion changes registry intent only. Explicit deployment records version/run/history separately; the API loads `models:/ieee_fraud_lgbm/1`. Real-model health, immutable readiness, single/batch prediction, stored threshold, train/serve parity and malformed-request 422 behavior passed. Safe handmade values were used for API checks; no restricted row fixture was published.

## SYNTHETIC SMOKE / PLUMBING EVIDENCE

Unit, integration, CI and container lifecycle tests use synthetic fixtures. They verify serialization and lifecycle behavior only. Their metrics are not IEEE-CIS performance and did not choose this release's configuration.

## Limitations, monitoring and operational consequences

Final-test fraud rate is 0.03480430340592226; imbalance makes accuracy misleading. At the frozen threshold, 1,459 fraud cases were missed and 2,042 legitimate transactions flagged. False negatives carry loss risk; false positives burden customers and reviewers. These values do not establish performance on current production traffic, other payment products, demographics, or future drift. No fairness/subgroup assessment or external validation was conducted; human review and jurisdiction-specific safeguards are required before operational use.

Monitoring supplies deterministic offline feature/prediction drift reports with provenance. Prediction persistence, delayed-ground-truth performance monitoring and alert integration remain deferred. Retraining is manual and must repeat leakage-safe promotion and separate deployment. Model, promotion, deployment, monitoring and security redesign are non-goals of this release.

The runtime is digest-pinned Wolfi/Python 3.11.17 with MLflow 2.22.5 and 38 locked OS packages. Verified Phase 5 security evidence has zero OS HIGH/CRITICAL findings and **22 accepted Python residual findings per image** (14 HIGH, 8 CRITICAL), expiring 2026-11-01. This is accepted risk, not vulnerability-free operation. Local model execution used macOS arm64 Python 3.11.15; Linux amd64 containers were validated independently with synthetic artifacts, not this private canonical binary. Production-like MinIO/S3/NGINX E2E remains externally limited; no complete production-stack PASS is claimed.

## Reproducibility

See [canonical reproduction guide](canonical-release.md), [configuration](../releases/ieee-cis-v1/canonical_config.json), [release manifest](../releases/ieee-cis-v1/release_manifest.json) and [security evidence](security/runtime-strategy.md). File, split, configuration, model, promotion, deployment and final-report identities are cross-checked by `python -m scripts.verify_canonical_release`. Exact floating-point results can depend on architecture/thread/library implementation; source and row identities must match, and new results must remain separately labelled reproductions.

## Release identity and licensing

The annotated [`ieee-cis-v1` release](https://github.com/bulsahkecici/end-to-end-mlops-fraud-detection/releases/tag/ieee-cis-v1) targets commit `243212ce211ceedb02e7230a53f4017b33d3decb`. Training source and pre-final review commits remain separately recorded in the immutable evidence manifest.

Repository source code is [MIT licensed](../LICENSE), copyright Bulşah Keçici. IEEE-CIS data is historical competition data governed by its own [Kaggle terms](https://www.kaggle.com/competitions/ieee-fraud-detection/rules); it and the private canonical model binary are not redistributed. Source licensing does not grant rights to those artifacts.

**Production-like E2E = NOT VERIFIED:** legacy Community `minio/minio` distribution is unavailable/access denied; failed workflow `37009722147` remains the evidence. The verified local-lite real-data lifecycle does not establish production readiness for that stack.

Canonical deployment identity: `20261002T125321465784Z-4cf8f6d7fe324477b6f25ae0bfa4e886`, immutable version `1`, run `a7d516e70f714caeb67f35bd2c30762f`. Prometheus exposes runtime request/inference metrics; Gitleaks, Trivy and Syft provide secret, vulnerability and SPDX evidence. [Monitoring](monitoring.md), [deployment](deployment.md).
