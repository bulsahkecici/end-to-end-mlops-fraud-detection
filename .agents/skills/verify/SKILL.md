---
name: verify
description: Verify repository changes before completing a phase or claiming success, selecting checks by changed surface and reporting actual command results.
---

# Verify repository changes

Use this workflow at the end of every production-hardening phase and whenever verification is requested.

## Workflow

1. Read `AGENTS.md` and `docs/PROJECT_STATE.md`, then inspect the change before running checks:

   ```bash
   git status --short
   git diff --check
   git diff --stat
   git diff
   ```

2. Identify the changed surface and run every relevant check from the matrix below. Prefer the repository's `.venv` interpreter when present.

   | Surface | Required checks |
   |---|---|
   | Python source or tests | `.venv/bin/python -m ruff check src tests`; `.venv/bin/python -m black --check src tests`; `.venv/bin/python -m mypy src` |
   | Focused behavior | `.venv/bin/python -m pytest tests/unit -q`; relevant targeted tests |
   | Cross-component behavior | `.venv/bin/python -m pytest tests/integration -q` |
   | Phase completion affecting Python behavior | `.venv/bin/python -m pytest --cov=src --cov-report=term-missing --cov-fail-under=75` |
   | Compose or Docker configuration | `docker compose --profile local-lite config`; `docker compose --profile production-like config`; relevant image build |
   | Serving, registry, deployment, Docker, or CI | relevant Docker smoke checks and/or `.venv/bin/python scripts/validate_e2e.py` |
   | Production-like lifecycle | follow `.agents/skills/docker-e2e/SKILL.md` |

3. Reinspect `git status --short` and the full diff after checks. Confirm generated files or formatter changes did not expand scope.

## Rules

- Stop and report a relevant failing command; never hide, downgrade, or work around it by weakening tests.
- When possible, reproduce a suspected pre-existing failure against the untouched baseline or cite prior recorded evidence. Clearly label pre-existing versus introduced failures; neither may be described as passing.
- Do not run heavyweight Docker/E2E checks when the diff is demonstrably limited to prose or control metadata. State why they were not applicable.
- Do not mutate registry aliases, persistent volumes, or external systems merely to verify an unrelated change.
- Report every command actually executed with its exit status. List relevant checks not run and the reason.
- A phase is not verified until `docs/PROJECT_STATE.md` reflects the resulting state and the final diff passes `git diff --check`.
