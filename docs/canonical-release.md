# Reproduce IEEE-CIS v1

The published release is **REAL IEEE-CIS EVIDENCE**, not a synthetic benchmark.
The [release manifest](../releases/ieee-cis-v1/release_manifest.json) links the
original source/configuration, dataset, splits, run, model, deployment and report.
Reproduction creates new run IDs, registry versions and deployment IDs; preserve
those as a separate reproduction rather than overwriting historical evidence.

## Access and environment

Accept the IEEE-CIS competition rules and use your own legitimate Kaggle access.
Download the original `train_transaction.csv`, `train_identity.csv`,
`test_transaction.csv`, `test_identity.csv` into ignored
`data/processed/ieee-fraud-detection/`. Do not publish those files, model input
examples, prediction rows, or frozen promotion Parquet. The competition test is
unlabelled and is not the release's final temporal holdout.

Run from the repository root, preferably a clean checkout of the release commit.
The original run's training source is
`81cfdea4ccf98305e7e1aa5a6105c8cddd10996d`; replay tooling was added later without
changing modeling source. Python 3.11.15/macOS arm64 produced the published model;
exact library versions are in `canonical_config.json`. Linux serving images use
Python 3.11.17. Parallel/native implementations can cause numeric variation;
source/split identities must match and variant selection must remain pre-final.
Allow sufficient RAM (original host: 36 GiB) for all 590,540 labelled rows.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip check
python -m scripts.verify_canonical_release
mkdir -p artifacts/reproduction/ieee-cis-v1
export MLFLOW_TRACKING_URI="sqlite:///artifacts/reproduction/ieee-cis-v1/mlflow.db"
export DEPLOYMENT_STATE_PATH="artifacts/reproduction/ieee-cis-v1/deployment/current.json"
export MODEL_NAME=ieee_fraud_lgbm
export MLFLOW_EXPERIMENT_NAME=ieee-cis-v1
export SAMPLE_ROWS=0
export RANDOM_SEED=42
export SPLIT_STRATEGY=temporal
export SAMPLING_STRATEGY=time_ordered
export THRESHOLD_STRATEGY=best_f1
export TRAIN_RATIO=0.70 VAL_RATIO=0.15 TEST_RATIO=0.15
export FALSE_NEGATIVE_COST=25 FALSE_POSITIVE_COST=1
export MIN_PR_AUC=0.10 MIN_RECALL=0.10 MAX_CHAMPION_REGRESSION=0.02
export CANDIDATE_ALIAS=candidate CHAMPION_ALIAS=champion
export API_KEY_ENABLED=false
```

Use a fresh directory/database; do not reuse the canonical or synthetic stores.
The replay command checks all four source hashes against the published dataset
manifest, verifies the preregistration, freezes full-data/default variant settings,
creates the isolated experiment with local artifacts, and records safe training
metadata. Environment variables never need personal absolute paths or secrets.

## Frozen configuration and training

[canonical_config.json](../releases/ieee-cis-v1/canonical_config.json) fixes full
input, seed 42, stable temporal 70/15/15 outer split, half-development promotion,
half-selection-pool early stopping, ordinal/drop encoding, no calibration,
`best_f1` selection-only threshold, 500 maximum trees, 31 leaves, learning rate
0.05, column sampling 0.9 and row sampling 0.8 every 5 iterations. Early stopping
uses calibration-fit AUC with patience 20. Final test is reporting-only.

```bash
python -m scripts.run_canonical_training \
  --output-directory artifacts/reproduction/ieee-cis-v1
```

The original release used equivalent `run_training(data_source='ieee', seed=42,
categorical_strategy='ordinal_drop_high_cardinality', calibration_strategy='none',
experiment_variant_id='ieee-cis-v1-ordinal-none')` with the environment above and
an explicitly created local experiment/artifact root. The source files were
audited and all partition fingerprints preregistered before that run. To audit a
new release, `python -m scripts.canonical_release` refuses to overwrite the
existing preregistration; use an isolated clean worktree/evidence destination and
commit the frozen plan before any final scoring. Existing v1 hashes should be
verified, not regenerated over the historical evidence.

## Promotion and deployment

```bash
python -m src.registry.promote \
  > artifacts/reproduction/ieee-cis-v1/promotion_manifest.json
python -m src.deployment.lifecycle deploy --expected-version 1 \
  > artifacts/reproduction/ieee-cis-v1/deployment_manifest.json
python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8326
```

Version `1` is expected only in a fresh registry. If your immutable version differs,
use the version returned by training throughout. Promotion must pass normally;
never manually assign champion or compare real evidence with synthetic identities.
Keep the API terminal running. In another terminal with the same environment:

```bash
python -m scripts.validate_canonical_serving \
  --directory artifacts/reproduction/ieee-cis-v1
```

This verifies health, readiness version/run, stored threshold, single and batch
prediction parity, and malformed request semantics using unrestricted handmade
values. It writes only safe serving evidence.

## Review before final reporting

Copy the published safe configuration/dataset/split/preregistration JSONs to the
reproduction directory; check the new model metadata against those source and
split hashes. Run:

```bash
cp releases/ieee-cis-v1/canonical_config.json \
   releases/ieee-cis-v1/dataset_manifest.json \
   releases/ieee-cis-v1/split_manifest.json \
   releases/ieee-cis-v1/preregistration.json artifacts/reproduction/ieee-cis-v1/
python -m scripts.verify_canonical_release --pre-final \
  --directory artifacts/reproduction/ieee-cis-v1
```

Review preprocessing train-only fitting, early-stopping/calibration-fit rows,
selection-only threshold, distinct promotion rows, immutable version/run,
deployment/serving agreement, frozen hashes and zero prior final scoring. Record
that review separately. Do not inspect final metrics before this gate or revise
the model based on its holdout score.

## Explicit final report

Only after the gate, execute once with the immutable version:

```bash
python -m src.modeling.final_test --model-uri models:/ieee_fraud_lgbm/1 \
  > artifacts/reproduction/ieee-cis-v1/final_test_metrics.json
```

Cross-check final identity against the preregistered 88,581-row fingerprint,
model threshold, source file hashes and new deployment/run identities. A command
bug may be fixed and the same frozen model/rows reported again with an explicit
failure history; changing variants after seeing metrics is prohibited. The v1
release required one successful final invocation and no retry.

The historical `release_manifest.json` contains exact byte checksums for its safe
JSON evidence; a reproduction must create its own manifest with its new run,
version, source commit, deployment, final-report count and evidence checksums.
`python -m scripts.verify_canonical_release` validates the published historical
chain offline. Preserve differences rather than modifying historical metrics.

## Verification and limits

Run the full tests, coverage, Ruff/Black/mypy, `pip check`, reviewed dependency
audit, both Compose renders and diff checks listed in README. Synthetic CI E2E
is separate from this real lifecycle. The production-like validator is
`python scripts/validate_production_e2e.py`; record actual failures and teardown.
The obsolete Community MinIO image remains an external infrastructure limitation;
do not substitute a new product or label an unverified stack PASS.
