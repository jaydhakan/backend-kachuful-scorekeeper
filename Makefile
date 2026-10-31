# Docker Compose v2 plugin when present, else the standalone docker-compose binary.
COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo docker-compose)

.PHONY: dev db migrate verify lint format typecheck test perf

db:
	$(COMPOSE) up -d --wait db

migrate:
	uv run alembic upgrade head

dev: db migrate
	uv run python -m app

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy app

test:
	uv run pytest

verify: lint typecheck test

perf:
	uv run pytest -m perf tests/perf
