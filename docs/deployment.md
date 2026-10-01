# Deployment

## Profiles

`docker-compose.yml` defines two independent profiles. Pick one: local-lite
publishes loopback development ports, while production-like publishes only
the NGINX front door. They are not meant to run together.

### `local-lite` (default, no credentials)

```bash
docker compose --profile local-lite up -d mlflow
```

- `mlflow`: sqlite backend store + local filesystem artifact store
  (`./mlflow_db`).
- `api`: builds from `Dockerfile.api`, depends on `mlflow` being healthy, and
  requires a valid explicit deployment state before it becomes healthy.
- `nginx`: rate-limited reverse proxy in front of `api`, published on
  loopback `:8080` (see "Rate limiting" below). `api` and MLflow also stay
  available on loopback `:8000` and `:5000` for local debugging. Local-lite
  is a developer profile, not an internet-facing deployment.

### `production-like` (Postgres + MinIO)

```bash
cp .env.example .env   # then fill in POSTGRES_PASSWORD, MINIO_ROOT_USER/PASSWORD
docker compose --profile production-like up -d postgres minio minio-init mlflow-prod
```

- `postgres`: MLflow tracking/registry backend store.
- `minio` + `minio-init`: S3-compatible artifact store; `minio-init` creates
  the artifact bucket once MinIO is healthy.
- `mlflow-prod`: MLflow server pointed at Postgres + MinIO.
- `api-prod`: same image as `api`, points `MLFLOW_TRACKING_URI` at
  `mlflow-prod`.
- `nginx-prod`: same rate-limiting proxy as `nginx`, in front of `api-prod`.

Only `nginx-prod` publishes a production-like host port. `api-prod`,
`mlflow-prod`, MinIO, and the MinIO console have no host port mapping and are
reachable only by services on the internal `mlops` Docker network. This
prevents a normal host-accessible API bypass around NGINX and does not present
the unauthenticated MLflow server or object store as internet-safe. Docker
network membership is a containment boundary, not tenant-grade MLflow
authentication; do not attach untrusted containers to this network.

The MLflow server image is based on `mlflow==2.22.5`, matching the Python
runtime, and adds the pinned Postgres/S3 drivers used by this profile. Compose
retains the repository's pre-existing public MinIO server/client image intent;
the Community repository is archived, Community distribution has moved to
source-only, and those `latest` container references are now obsolete and
unavailable. Phase 4 does not build or redistribute MinIO. It also does not
silently substitute AIStor, whose enterprise/evaluation licensing would change
the project's product assumption. Choosing a maintained S3-compatible object
store is a future infrastructure decision; this blocker is not expected to
resolve automatically.

Compose refuses to start `production-like` if `POSTGRES_PASSWORD`,
`MINIO_ROOT_USER`, or `MINIO_ROOT_PASSWORD` aren't set (via `${VAR:?...}`)
— there is no silent fallback to a blank/default credential.

## Full local workflow

```bash
make install
docker compose --profile local-lite up -d mlflow
make train-smoke                # or: make train-ieee (needs real data, see README)
make promote                    # gates candidate -> champion
make deploy                     # freezes champion to an immutable deployed version
make deployment-status          # inspect exact deployed model/run/provenance
docker compose --profile local-lite up -d --build --force-recreate api nginx
make smoke-test                 # scripts/validate_e2e.py, end to end
```

The container restart/recreation is intentional. There is no background
deployment-state polling and no alias-following hot reload.

## Promotion is not deployment

`src/modeling/train.py` only ever assigns the `candidate` alias to a new
model version — nothing is served to `champion` traffic automatically.
`make promote` (`python -m src.registry.promote`) runs the gate described
in `docs/model-card.md` and only then reassigns `champion`. This is
idempotent and safe to re-run.

**PROMOTION != DEPLOYMENT.** Moving `champion` records approval intent only.
It neither updates `artifacts/deployment/current.json` nor reloads the API.

**CHAMPION ALIAS CHANGE != RUNNING MODEL CHANGE.** The deployment command
resolves `champion` once, verifies and loads that immutable model version,
rechecks that the alias remained stable, and atomically records the exact
target. FastAPI reads only that record and loads
`models:/<model-name>/<version>` at startup. An alias move by itself cannot
change either an already-running process or the next restart's target.

## Deployment state and commands

The default current-state file is `artifacts/deployment/current.json` and
immutable audit records live in `artifacts/deployment/history/`. Override the
path for native commands with `DEPLOYMENT_STATE_PATH`; Compose reads the host
directory from `DEPLOYMENT_STATE_DIR` and mounts it read-only at `/deployment`.
State contains the model name/version, run ID, source alias, UTC timestamp,
Git context when available, and the preceding deployment identity/version.

```bash
# Deploy only the current approved champion. This fails if approval is absent,
# the registry/artifact is unavailable, or the alias moves during validation.
python -m src.deployment.lifecycle deploy

# Optional guard: still cannot deploy this version unless it is champion.
python -m src.deployment.lifecycle deploy --expected-version 3

# Inspect the exact immutable target selected for serving.
python -m src.deployment.lifecycle inspect

# Restore the immediately previous known-good deployment-history target.
python -m src.deployment.lifecycle rollback
```

All unsafe/invalid operations exit non-zero. Each transition is validated
before write. The current state is written through a same-directory temporary
file, fsynced, and atomically replaced; each history event is fsynced and
created exclusively, so an existing immutable history identity cannot be
overwritten. A failed deploy/rollback cannot partially overwrite the current
valid state. Re-deploying the same champion and repeating a completed rollback
are idempotent.

After `deploy` or `rollback`, explicitly recreate/restart the API, then confirm
the immutable version and run ID at `/ready`:

```bash
docker compose --profile local-lite up -d --build --force-recreate api
docker compose --profile local-lite up -d nginx
curl -s http://localhost:8080/ready
```

Use `api-prod`/`nginx-prod` with the `production-like` profile. The repeatable
isolated production-like smoke—including Postgres, MinIO bucket initialization,
MLflow, two deployments, no-hot-swap proof, NGINX prediction, and rollback—is:

```bash
make production-e2e
```

It uses disposable credentials, an isolated Compose project, temporary
deployment state, and always runs `down -v` for that project. The base
production-like profile remains NGINX-only; the validator generates a temporary
Compose override that binds API, MLflow, and MinIO to random loopback-only ports
for host-side lifecycle probes. The override exists only for the isolated
validator run and never exposes those services on `0.0.0.0`. The validator is
retained for an explicit future object-store decision, but currently exits
non-zero at the obsolete Community MinIO dependency and must not be interpreted
as a production-like PASS.

## Scaling / process model

`Dockerfile.api` runs one Uvicorn worker per container. The existing
`prometheus_client` metrics are process-local, so multiple workers would
produce incomplete/conflicting metric views without Prometheus multiprocess
configuration. Scale horizontally with additional containers behind a shared
proxy/load balancer when needed; multiprocess metrics are deliberately outside
Phase 4.

## Security notes

- **Batch/body size limits**: `API_MAX_BATCH_SIZE` (records per request)
  and `API_MAX_REQUEST_BYTES` (raw body size) are enforced before any
  model work happens (`src/api/schemas.py`, `src/api/middleware.py`).
- **API key**: off by default (`API_KEY_ENABLED=false`) for frictionless
  local development; set to `true` in any shared/production environment.
  Comparisons are constant-time. `/health`, `/ready`, and `/metrics` are the
  only unauthenticated application routes. Valid browser CORS preflight is
  handled by CORS before endpoint authentication and cannot invoke inference.
- **CORS**: `CORS_ALLOW_ORIGINS` defaults to `*`; set to your actual
  frontend origin(s) in production.
- **Timeouts**: this project does not set an explicit per-request timeout
  in the FastAPI app itself — model inference on a single batch is
  bounded and fast (millisecond-scale), so the practical timeout boundary
  is whatever the reverse proxy / load balancer in front of the API
  enforces (e.g. an nginx/ALB idle timeout). If you deploy without a
  proxy, consider adding `uvicorn --timeout-keep-alive` tuning.
- **Rate limiting**: implemented at the reverse-proxy layer
  (`deploy/nginx/nginx.conf.template`), not in the FastAPI app itself — an
  in-process, per-worker limiter would enforce a separate, incorrect
  counter per uvicorn worker as soon as `--workers > 1` (each worker sees
  only its own share of requests, so the effective limit would silently
  scale with worker count instead of being a real ceiling). nginx enforces
  one shared limit per client IP (`limit_req_zone $binary_remote_addr`)
  across the whole upstream, ahead of the batch/body-size limits already
  enforced in-app. Configurable via `RATE_LIMIT_RPS` (steady-state
  requests/sec/IP, default 10) and `RATE_LIMIT_BURST` (extra burst
  capacity before `429`s start, default 20) in `.env`. `/health`, `/ready`,
  `/metrics` are exempted so monitoring probes are never throttled.
  Verified: firing 40 rapid `/predict` requests through `nginx` on
  defaults returns `200` for the first ~30 (burst + one tick of steady
  rate) then `429 Too Many Requests` for the rest; the same burst against
  `/health` stays `200` throughout.
- **Dependency audit**: `make security-audit` runs `pip check` and the pinned
  `pip-audit==2.10.1` against `requirements-dev.txt` (which includes runtime
  requirements). The checked-in reviewed baseline fails CI for new findings,
  removed/stale findings, newly available same-major fixes, audit collection
  failures, or malformed results; it does not turn already-reviewed findings
  into a permanently red job.
- **Accepted dependency risk (reviewed 2026-10-01)**: `pip-audit` reports 54
  raw findings (28 unique advisory IDs) across `mlflow==2.22.5` and
  `pyarrow==17.0.0`. All listed MLflow fixes require MLflow 3.x, with several
  requiring substantially later 3.x releases or having no fixed version.
  Phase 5 Slice 1 therefore does not make an unproven registry/artifact/model
  compatibility migration. MLflow is runtime-reachable and must remain on a
  trusted internal network; features mentioned by several advisories (basic
  auth, jobs, AI Gateway, webhooks, model serving) are not enabled here, but
  artifact upload/deserialization findings remain relevant if that boundary
  is compromised. The PyArrow advisory concerns a C++ pre-buffering API that
  its advisory says is not exposed through Python bindings, so the repository's
  Python Parquet usage is not believed reachable. The selected PyPI audit
  service did not return severity scores, so advisory IDs and reachability are
  recorded rather than invented severities.
- **Tool coverage**: Gitleaks, Trivy, and Syft are not installed in the current
  environment and are not silently substituted. Their secret-scan,
  image-vulnerability, and SBOM coverage remains a documented gap until pinned
  versions and reviewed baselines can be added without hiding accepted risk.

## Windows

All `make` targets are thin wrappers over plain Python/docker commands —
see the comment block at the top of the `Makefile` for the direct
PowerShell-friendly equivalent of each target if `make` isn't available.
