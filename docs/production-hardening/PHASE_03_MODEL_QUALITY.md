# PHASE 3 — Model Quality

## Goal

Evaluate categorical handling, high-cardinality strategy, and probability calibration through reproducible, leakage-safe benchmarks.

## Scope

- Benchmark improved categorical and high-cardinality approaches against the preserved baseline.
- Evaluate probability calibration and its effect on quality and business cost.
- Record comparable experiment evidence and retain only demonstrated improvements.

## Non-goals

- Promotion-policy, deployment-lifecycle, monitoring, or security redesign.
- Replacing the baseline without comparable evidence.

## Required invariants

- Follow `AGENTS.md` and the `ml-evaluation` skill.
- Fit all feature and calibration components without validation/test leakage.
- Use validation for experiment choice and final test only once after the choice is frozen.
- Preserve the single canonical train/serve pipeline.

## Expected implementation surface

- Feature pipeline, model training/evaluation, experiment configuration, tests, and model-quality documentation.

## Required tests/verification

- Leakage and train/serve parity tests plus reproducible baseline-versus-experiment benchmarks.
- Required discrimination, classification, calibration, and business-cost metrics on comparable data.
- Full verification via the `verify` skill.

## Completion criteria

- The selected approach has reproducible evidence of improvement or the existing baseline is explicitly retained.
- Calibration and high-cardinality decisions are documented with dataset fingerprints and metrics.
- `docs/PROJECT_STATE.md` is updated.

## Implemented design

Routine training now reserves the final test without calculating its target
summary, predictions, metrics, MLflow metrics, or `test_metrics.json` artifact.
Final-test scoring is available only through the explicit command:

```bash
python -m src.modeling.final_test --model-uri runs:/<run-id>/model
```

That report carries a final-row fingerprint and labels synthetic evidence as
`synthetic_plumbing_only`. It does not modify promotion or registry state.

The development data lifecycle is now:

1. Fit schema, preprocessing, imputers, encoders, and LightGBM on train only.
2. Use the earlier half of selection-validation for LightGBM early stopping and
   optional calibration fitting.
3. Use the disjoint later half for variant metrics and threshold selection.
4. Preserve the frozen promotion partition exclusively for the Phase 2 gate.
5. Leave final test untouched until the explicit reporting command.

Categorical variants are explicit:

- `ordinal_drop_high_cardinality` preserves the Phase 2 baseline.
- `frequency_high_cardinality` retains high-cardinality columns with
  label-independent relative-frequency maps fitted on train only. Unknown
  categories map to zero. The maps serialize inside the canonical sklearn
  pipeline and need no serving-side state.

Calibration variants are `none`, `sigmoid`, and `isotonic`. Sigmoid uses a
logistic map over clipped log-odds; isotonic uses an out-of-bounds-clipped
monotonic map. Each wraps the fitted estimator inside the canonical pipeline,
so promotion and serving continue to consume the same `fraud_probability`.

Each MLflow run now records a variant ID, feature/categorical/calibration
strategies, complete model parameters, random seed, source fingerprint,
calibration-fit fingerprint, exact selection-evaluation fingerprint, selection
metrics, and the unchanged Phase 2 promotion manifest. Routine runs contain no
final-test performance evidence.

## Reproducible synthetic plumbing comparison

The repository does not contain the real IEEE-CIS files. The following fixed
comparison therefore validates experiment comparability and code paths only;
it is **not IEEE-CIS model-quality evidence** and did not authorize replacing
the baseline.

- Generator: `make_synthetic_transactions:v1`
- Rows: 4,000; seed: 314; temporal split
- High-cardinality cutoff: 2 (deliberately low to exercise both strategies)
- Selection fingerprint for every variant:
  `5b4e8dcff21daaabdd22a91e1a610eb131e9c1fc03437081e4e40e2da12609ed`
- Expected-cost weights: false negative 25, false positive 1

| Variant | ROC-AUC | PR-AUC | Precision | Recall | F1 | Log loss | Brier | Cost / row |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline / none | 0.9211 | 0.4939 | 0.7143 | 0.7143 | 0.7143 | 0.1585 | 0.0403 | 0.3467 |
| frequency / none | 0.9825 | 0.8298 | 0.8571 | 0.8571 | 0.8571 | 0.1543 | 0.0395 | 0.1733 |
| baseline / sigmoid | 0.9211 | 0.4939 | 0.7143 | 0.7143 | 0.7143 | 0.1576 | 0.0415 | 0.3467 |
| baseline / isotonic | 0.8796 | 0.3852 | 0.5000 | 0.7143 | 0.5882 | 0.3255 | 0.0332 | 0.3667 |

Decision: retain `ordinal_drop_high_cardinality` plus `none` as defaults. The
frequency and calibration variants are first-class, reproducible options, but
must be selected only after comparing them on the same real IEEE-CIS
selection-validation fingerprint. The promotion set remains a gate and the
final test remains a one-time release report; neither may choose a variant.
