.PHONY: install lint format typecheck test coverage security-audit security-secret-scan \
        security-image-build security-image-evidence security-evidence \
        train-smoke train-ieee promote deploy deployment-status rollback serve \
        docker-build docker-up docker-down smoke-test production-e2e drift-report \
        up down

SECURITY_EVIDENCE_DIR ?= reports/security-evidence
SECURITY_API_IMAGE ?= ieee-fraud-api:security
SECURITY_MLFLOW_IMAGE ?= ieee-fraud-mlflow:security

# Windows (PowerShell) users: these targets are thin wrappers over plain
# Python/pip/docker commands. If `make` isn't available, run the command on
# the right-hand side of each target directly, e.g.:
#   python -m pip install -r requirements-dev.txt   (install)
#   python -m pytest                                (test)
#   python -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000   (serve)

install:
	pip install -r requirements-dev.txt

lint:
	ruff check src tests

format:
	black src tests
	ruff check --fix src tests

typecheck:
	mypy src

test:
	pytest

coverage:
	pytest --cov=src --cov-report=term-missing

security-audit:
	python -m pip check
	python scripts/audit_python_dependencies.py

security-secret-scan:
	python scripts/security_evidence.py secrets --repository . --output-dir $(SECURITY_EVIDENCE_DIR) --source-revision "$$(git rev-parse HEAD)"

security-image-build:
	docker build --pull --no-cache -f Dockerfile.api -t $(SECURITY_API_IMAGE) .
	docker build --pull --no-cache -f Dockerfile.mlflow -t $(SECURITY_MLFLOW_IMAGE) .

security-image-evidence: security-image-build
	python scripts/security_evidence.py images --image api=$(SECURITY_API_IMAGE) --image mlflow=$(SECURITY_MLFLOW_IMAGE) --output-dir $(SECURITY_EVIDENCE_DIR) --source-revision "$$(git rev-parse HEAD)"

security-evidence: security-audit security-secret-scan security-image-evidence

train-smoke:
	python -m src.modeling.train --data-source synthetic --n-synthetic 4000

train-ieee:
	python -m src.modeling.train --data-source ieee

promote:
	python -m src.registry.promote

deploy:
	python -m src.deployment.lifecycle deploy

deployment-status:
	python -m src.deployment.lifecycle inspect

rollback:
	python -m src.deployment.lifecycle rollback

serve:
	python -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000

docker-build:
	docker build -f Dockerfile.api -t ieee-fraud-api:local .

docker-up:
	docker compose --profile local-lite up -d

docker-down:
	docker compose --profile local-lite down
	docker compose --profile production-like down

smoke-test:
	python scripts/validate_e2e.py

production-e2e:
	python scripts/validate_production_e2e.py

drift-report:
	python -m src.monitoring.drift $(DRIFT_ARGS)

# Aliases kept for backwards compatibility with the original Makefile.
up: docker-up
down: docker-down
