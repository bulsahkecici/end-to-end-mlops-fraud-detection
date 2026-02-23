.PHONY: up down train-smoke train-ieee serve format test

up:
	docker compose up -d

down:
	docker compose down

train-smoke:
	PYTHONPATH=. python src/train.py

train-ieee:
	PYTHONPATH=. python src/train_ieee.py

serve:
	uvicorn src.serve.app:app --host 0.0.0.0 --port 8000

format:
	@echo "Optional: black src/ && isort src/"

test:
	@echo "Optional: pytest tests/"
