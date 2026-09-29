# Repository invariants

- Fit preprocessing, schema inference, imputers, and encoders on training data only. Validation and test data may only be transformed.
- Never use the final test split for model selection, threshold selection, calibration choice, early stopping, or promotion decisions.
- Treat candidate and champion metrics as comparable only when both models were scored on the same frozen canonical evaluation dataset/version, identified by a reproducible fingerprint.
- Training and serving must continue to use one canonical fitted preprocessing-and-model path; do not add a separate serving transform.
- Never present synthetic-data metrics as performance on the real IEEE-CIS dataset.
- Do not weaken, delete, skip, or bypass tests merely to make a change pass.
- Do not silently broaden inference acceptance. Any contract change requires explicit validation semantics, tests, and documentation.
- Training may register or assign `candidate`; it must never directly promote a model to `champion`.
- Promotion and deployment are separate lifecycle concepts and must remain independently traceable.
- Stay within the active phase scope. Make only a minimal prerequisite change when a blocking issue makes it unavoidable, and document it.
- Finish every phase by following `.agents/skills/verify/SKILL.md` and update `docs/PROJECT_STATE.md` with verified facts.
- Do not claim success unless the relevant commands were actually executed successfully; report failures and skipped checks explicitly.
