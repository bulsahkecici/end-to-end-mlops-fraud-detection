.PHONY: up down train-smoke train-ieee promote serve format lint typecheck test coverage

up:
	docker compose up -d

down:
	docker compose down

train-smoke:
	python -m src.modeling.train --data-source synthetic --n-synthetic 4000

train-ieee:
	python -m src.modeling.train --data-source ieee

promote:
	python -m src.registry.promote

serve:
	python -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000

format:
	black src tests

lint:
	ruff check src tests

typecheck:
	mypy src

test:
	pytest

coverage:
	pytest --cov=src --cov-report=term-missing
