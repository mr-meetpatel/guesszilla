.DEFAULT_GOAL := help
UV ?= uv
COMPOSE ?= docker compose
JOB_ID ?=

.PHONY: help setup dev serve worker combined check format test test-integration migrate jobs retry docker-up docker-down docker-logs docker-supabase db-up validate-render
help:
	@echo "setup dev serve worker combined check format test test-integration migrate jobs retry"
	@echo "docker-up docker-down docker-logs docker-supabase db-up"
	@echo "validate-render (requires network access)"
setup:
	pyenv install -s $$(cat .python-version)
	pyenv local $$(cat .python-version)
	$(UV) sync --frozen --python "$$(pyenv which python)"
dev:
	$(UV) run --frozen wordle-bot dev
serve:
	$(UV) run --frozen wordle-bot serve
worker:
	$(UV) run --frozen wordle-bot worker
combined:
	$(UV) run --frozen wordle-bot combined
check:
	$(UV) run --frozen ruff check .
	$(UV) run --frozen ruff format --check .
	$(UV) run --frozen mypy
format:
	$(UV) run --frozen ruff check --fix .
	$(UV) run --frozen ruff format .
test:
	$(UV) run --frozen pytest -m 'not integration'
test-integration:
	$(UV) run --frozen pytest -m integration
migrate:
	$(UV) run --frozen wordle-bot migrate
jobs:
	$(UV) run --frozen wordle-bot jobs
retry:
	@test -n "$(JOB_ID)" || (echo "Use make retry JOB_ID=123"; exit 1)
	$(UV) run --frozen wordle-bot retry $(JOB_ID)
docker-up:
	$(COMPOSE) up --build -d
docker-down:
	$(COMPOSE) down
docker-logs:
	$(COMPOSE) logs -f web worker
docker-supabase:
	$(COMPOSE) -f compose.supabase.yaml up --build -d
db-up:
	$(COMPOSE) up -d db
validate-render:
	$(UV) run --frozen python scripts/validate_render.py
