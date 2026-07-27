# Deployment

## Profiles

`docker-compose.yml` defines two independent profiles — pick one, they are
not meant to run together (both bind port 5000/8000):

### `local-lite` (default, no credentials)

```bash
docker compose --profile local-lite up -d      # or: make docker-up
```

- `mlflow`: sqlite backend store + local filesystem artifact store
  (`./mlflow_db`).
- `api`: builds from `Dockerfile.api`, depends on `mlflow` being healthy.
- `nginx`: rate-limited reverse proxy in front of `api`, published on
  `:8080` (see "Rate limiting" below). `api` itself stays published on
  `:8000` too, for direct/debug access.

### `production-like` (Postgres + MinIO)

```bash
cp .env.example .env   # then fill in POSTGRES_PASSWORD, MINIO_ROOT_USER/PASSWORD
docker compose --profile production-like up -d
```

- `postgres`: MLflow tracking/registry backend store.
- `minio` + `minio-init`: S3-compatible artifact store; `minio-init` creates
  the artifact bucket once MinIO is healthy.
- `mlflow-prod`: MLflow server pointed at Postgres + MinIO.
- `api-prod`: same image as `api`, points `MLFLOW_TRACKING_URI` at
  `mlflow-prod`.
- `nginx-prod`: same rate-limiting proxy as `nginx`, in front of `api-prod`.

Compose refuses to start `production-like` if `POSTGRES_PASSWORD`,
`MINIO_ROOT_USER`, or `MINIO_ROOT_PASSWORD` aren't set (via `${VAR:?...}`)
— there is no silent fallback to a blank/default credential.

## Full local workflow

```bash
make install
make docker-up                 # starts mlflow (local-lite)
make train-smoke                # or: make train-ieee (needs real data, see README)
make promote                    # gates candidate -> champion
make serve                      # or: run the api container instead
make smoke-test                 # scripts/validate_e2e.py, end to end
```

## Promotion is a separate, deliberate step

`src/modeling/train.py` only ever assigns the `candidate` alias to a new
model version — nothing is served to `champion` traffic automatically.
`make promote` (`python -m src.registry.promote`) runs the gate described
in `docs/model-card.md` and only then reassigns `champion`. This is
idempotent and safe to re-run.

## Scaling / process model

`Dockerfile.api`'s default `CMD` runs uvicorn with `--workers 2`. Each
worker process loads its own copy of the model into memory at startup
(`src/api/dependencies.py`'s `lifespan` hook) — there is no shared model
cache across workers. Increase `--workers` for more throughput on a larger
host; each additional worker costs roughly one model's worth of RAM.

## Security notes

- **Batch/body size limits**: `API_MAX_BATCH_SIZE` (records per request)
  and `API_MAX_REQUEST_BYTES` (raw body size) are enforced before any
  model work happens (`src/api/schemas.py`, `src/api/middleware.py`).
- **API key**: off by default (`API_KEY_ENABLED=false`) for frictionless
  local development; set to `true` in any shared/production environment.
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
- **Dependency vulnerabilities**: see the "ci: add drift monitoring, patch
  known dependency vulnerabilities" commit message for the current
  `pip-audit` status. `mlflow` and `pyarrow` both have known CVEs only
  fixed in major-version bumps (mlflow 3.x, pyarrow 23.x) not attempted in
  this pass — see "Known limitations" in the final report.

## Windows

All `make` targets are thin wrappers over plain Python/docker commands —
see the comment block at the top of the `Makefile` for the direct
PowerShell-friendly equivalent of each target if `make` isn't available.
