.DEFAULT_GOAL := help
UV ?= uv
COMPOSE ?= docker compose
JOB_ID ?=
# Database suites reset shared tables. Keep make -j from overlapping them.
.NOTPARALLEL:
TEST_DB_NAME ?= wordle_test
export UV COMPOSE TEST_DB_NAME

.PHONY: help setup dev dev-api serve worker scheduler combined check format test test-unit test-backend-unit test-frontend-unit test-integration test-backend-e2e test-frontend-e2e test-e2e test-all test-db-prepare test-db-check test-browser-install migrate jobs retry docker-up docker-down docker-logs docker-supabase db-up validate-render api-client check-client logs health admin-bootstrap admin-reset import-legacy prune secrets
help:
	@echo "setup dev serve worker combined check format test test-integration migrate jobs retry"
	@echo "docker-up docker-down docker-logs docker-supabase db-up"
	@echo "validate-render (requires network access)"
	@echo "dev-api scheduler api-client check-client test-e2e logs health secrets admin-bootstrap admin-reset import-legacy prune"
	@echo "test-all: all suites using wordle_test in the existing Compose db (postgres:17.11-bookworm)"
	@echo "test-unit test-backend-unit test-frontend-unit test-integration test-backend-e2e test-frontend-e2e"
	@echo "test-db-prepare test-db-check test-browser-install; TEST_DB_NAME defaults to wordle_test"
setup:
	pyenv install -s $$(cat .python-version)
	pyenv local $$(cat .python-version)
	$(UV) sync --frozen --python "$$(pyenv which python)"
	pnpm install --frozen-lockfile
dev:
	pnpm dev
dev-api:
	$(UV) run --frozen wordle-bot dev
serve:
	$(UV) run --frozen wordle-bot serve
worker:
	$(UV) run --frozen wordle-bot worker
scheduler:
	$(UV) run --frozen wordle-bot scheduler
combined:
	$(UV) run --frozen wordle-bot combined
check:
	$(UV) run --frozen ruff check .
	$(UV) run --frozen ruff format --check .
	$(UV) run --frozen mypy
	pnpm check
	pnpm format:check
check-client:
	$(UV) run --frozen python scripts/export_openapi.py --check
	pnpm generate:client
	git diff --exit-code -- apps/admin/src/generated/api.d.ts
api-client:
	$(UV) run --frozen python scripts/export_openapi.py
	pnpm generate:client
format:
	$(UV) run --frozen ruff check --fix .
	$(UV) run --frozen ruff format .
	pnpm format
test: test-unit
test-unit:
	$(MAKE) --no-print-directory test-backend-unit
	$(MAKE) --no-print-directory test-frontend-unit
test-backend-unit:
	$(UV) run --frozen pytest -m 'not integration and not e2e'
test-frontend-unit:
	pnpm test
test-integration:
	$(UV) run --frozen python scripts/run_tests.py integration
test-backend-e2e:
	$(UV) run --frozen python scripts/run_tests.py backend-e2e
test-frontend-e2e:
	$(UV) run --frozen python scripts/run_tests.py frontend-e2e
test-e2e:
	$(UV) run --frozen python scripts/run_tests.py e2e
test-all:
	$(UV) run --frozen python scripts/run_tests.py all
test-db-prepare:
	$(UV) run --frozen python scripts/run_tests.py prepare
test-db-check:
	$(UV) run --frozen python scripts/run_tests.py check
test-browser-install:
	pnpm --filter @wordle/admin exec playwright install chromium
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
	$(COMPOSE) logs -f web worker scheduler frontend
logs: docker-logs
health:
	$(COMPOSE) ps
	$(COMPOSE) exec frontend wget -q -O - http://127.0.0.1:8080/health/ready
admin-bootstrap:
	$(COMPOSE) exec web wordle-bot admin-bootstrap
admin-reset:
	$(COMPOSE) exec web wordle-bot admin-reset
import-legacy:
	$(COMPOSE) exec web wordle-bot import-legacy
prune:
	$(COMPOSE) exec web wordle-bot prune
secrets:
	$(UV) run --frozen python -c "import secrets; from cryptography.fernet import Fernet; print('JWT_SECRET_KEY=' + secrets.token_urlsafe(48)); print('CREDENTIAL_ENCRYPTION_KEYS=' + Fernet.generate_key().decode())"
docker-supabase:
	$(COMPOSE) -f compose.supabase.yaml up --build -d
db-up:
	$(COMPOSE) up -d db
validate-render:
	$(UV) run --frozen python scripts/validate_render.py
