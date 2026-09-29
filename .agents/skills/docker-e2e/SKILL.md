---
name: docker-e2e
description: Validate the repository's production-like Postgres, MinIO, MLflow, API, and NGINX stack end to end with isolated state and clean teardown.
---

# Production-like Docker E2E

Use an isolated Compose project name, non-production credentials, non-conflicting host ports, and disposable volumes. Do not reuse or delete a developer's existing stack.

## Validation sequence

1. Validate the resolved `production-like` Compose configuration and build the API image.
2. Start Postgres, MinIO, bucket initialization, MLflow, API, and NGINX. Require container health where defined and inspect startup logs on failure.
3. Verify Postgres readiness, MinIO readiness and artifact bucket availability, and MLflow health/tracking access.
4. Register a clearly labeled synthetic smoke model through the normal training path, confirm the candidate artifact can be fetched from MinIO through MLflow, and exercise the normal promotion gate.
5. Apply the deployment/reload step separately from promotion. Verify API `/health`, `/ready`, loaded model version/source, NGINX reachability, and a valid `/predict` response through NGINX.
6. Capture command exit statuses and relevant model/run/version identifiers without printing credentials.
7. Always tear down the isolated project and its disposable volumes, including after failure. Confirm no test containers or networks remain.

Fail loudly on any missing service, unavailable model artifact, version mismatch, unhealthy readiness state, failed prediction, or incomplete teardown. Synthetic success proves plumbing only, not real IEEE-CIS quality.
