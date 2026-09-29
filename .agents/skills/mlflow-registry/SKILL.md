---
name: mlflow-registry
description: Change or review this repository's MLflow candidate, champion, promotion, deployment, rollback, and traceability lifecycle.
---

# MLflow registry lifecycle

- Training may create a registered version and assign `candidate`; it must never assign `champion` or deploy a model.
- Promotion is an explicit gate that may move `champion` only after loadability, signature, prediction, quality, and comparable-evaluation checks pass.
- Candidate and champion quality may be compared only after both are rescored on the same frozen canonical evaluation dataset/version. Reject promotion when dataset identity, fingerprints, predictions, or required metrics are missing.
- Promotion changes registry intent; deployment changes serving state. Record and test them separately. A running API must not be assumed to have reloaded merely because an alias moved.
- Preserve rollback information: previous champion version, promotion decision, deployment version, and a verified procedure to reassign the alias and redeploy/reload the prior artifact.
- Keep traceability from model version to run ID, commit SHA, code/environment, data and evaluation fingerprints, split/sampling configuration, feature schema, threshold, metrics, artifacts, promotion decision, and deployment record.
- Promotion logic must fail closed and remain idempotent. Never bypass a failed gate by mutating aliases manually as part of verification.
