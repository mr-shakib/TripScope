# TripScope developer commands. Run `make help` for the list.
SHELL := /bin/bash
SOURCE ?= yellow-2025-01
BACKEND := cd backend &&
FRONTEND := cd frontend &&

.PHONY: help env up down reset migrate pipeline pipeline-docker aggregates user api worker web app \
        test test-unit test-integration test-frontend e2e lint format

help: ## Show available targets
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

env: ## Create .env with random local secrets (refuses to overwrite)
	python3 scripts/generate_env.py

up: ## Start PostgreSQL, ClickHouse and SeaweedFS
	docker compose up -d --wait

down: ## Stop all containers (data volumes are kept)
	docker compose --profile app --profile pipeline down

reset: ## DESTRUCTIVE: stop containers and delete all TripScope data volumes
	@read -p "Delete all TripScope volumes (database, ClickHouse, lake)? [y/N] " ok && [ "$$ok" = y ]
	docker compose --profile app --profile pipeline down -v

migrate: ## Apply PostgreSQL (Alembic) and ClickHouse migrations from the host
	$(BACKEND) uv run alembic upgrade head
	$(BACKEND) uv run tripscope-pipeline migrate

pipeline: ## Run one manifest source end to end on the host (SOURCE=yellow-2025-01)
	$(BACKEND) uv run tripscope-pipeline run --source $(SOURCE)

aggregates: ## Rebuild pre-aggregates for every published month (no Spark needed)
	$(BACKEND) uv run tripscope-pipeline build-aggregates

pipeline-docker: ## Run one manifest source in the Spark container (SOURCE=yellow-2025-01)
	docker compose --profile pipeline run --rm pipeline run --source $(SOURCE)

user: ## Create a user interactively: make user EMAIL=a@b.org NAME="Ana" ROLE=analyst
	$(BACKEND) uv run tripscope-admin create-user --email "$(EMAIL)" --name "$(NAME)" --role "$(ROLE)"

api: ## Run the API with auto-reload on http://127.0.0.1:8000
	$(BACKEND) uv run uvicorn tripscope.api.app:create_app --factory --reload --host 127.0.0.1 --port 8000

worker: ## Process jobs queued from the UI/API on the host (Ctrl+C finishes the current job)
	$(BACKEND) uv run tripscope-pipeline worker

web: ## Run the Next.js dev server on http://127.0.0.1:3000 (rewrites /api to :8000)
	$(FRONTEND) npm run dev

app: ## Build and start API, worker and web containers on http://127.0.0.1:8080
	docker compose --profile app up -d --build --wait

test: test-unit test-integration test-frontend ## Run all automated tests except e2e

test-unit: ## Backend unit tests (includes local Spark tests)
	$(BACKEND) uv run pytest -m "not integration" -q

test-integration: ## Backend integration tests (needs `make up`; uses isolated *_test databases and bucket)
	$(BACKEND) uv run pytest -m integration -q

test-frontend: ## Frontend type check, lint and unit tests
	$(FRONTEND) npm run typecheck && npm run lint && npm test

e2e: ## Browser tests against a running stack (E2E_ADMIN_EMAIL/PASSWORD, optional E2E_VIEWER_*, E2E_BASE_URL)
	$(FRONTEND) npx playwright test

lint: ## Static checks for backend and frontend
	$(BACKEND) uv run ruff check src tests migrations && uv run ruff format --check src tests migrations && uv run mypy
	$(FRONTEND) npm run typecheck && npm run lint

format: ## Auto-format backend code
	$(BACKEND) uv run ruff format src tests migrations && uv run ruff check --fix src tests migrations
