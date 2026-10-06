# ===========================================================================
# Digital Worker Platform — developer commands
# Requires: uv, pnpm, GNU make, docker (for infra/docker targets)
# Run inside Git Bash on Windows.
# ===========================================================================

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# Load local .env (if present) and export its variables to all recipes.
ifneq (,$(wildcard .env))
include .env
export
endif

COMPOSE := docker compose --env-file .env -f infra/compose/docker-compose.yml

.PHONY: help bootstrap infra-up infra-down dev docker-up docker-down \
        db-migrate migrate lint format typecheck \
        test-unit coverage test-integration test-architecture test-contract test-hooks \
        test-e2e test-web test-all eval-smoke test-eval-smoke \
        generate-contracts new-context release-manifest release-manifest-check ci

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- bootstrap --
bootstrap: ## Install all Python + Node dependencies and local config
	uv sync --all-packages
	pnpm install
	@if [ ! -f .env ]; then cp .env.example .env && echo ">> created .env from .env.example — fill in secrets"; fi
	-uv run pre-commit install
	@echo ">> bootstrap complete"

# -------------------------------------------------------------------- infra --
# `--wait` reports any container that exits as a failure, and s3-setup is a
# one-shot bucket creator that always exits — so the health wait names the
# long-running services and s3-setup is started on its own.
# docgen is here rather than only in `full`: an agent offers document
# generation whenever the sandbox answers, and `make dev` runs the apps on the
# host. It costs one ~1GB image build the first time.
INFRA_SERVICES = postgres qdrant valkey s3 keycloak docgen docgen-gateway
# Same reason for the full stack: migrate, seed and s3-setup all run once and
# exit, so `--wait` on the whole profile reports a healthy stack as a failure.
FULL_SERVICES = $(INFRA_SERVICES) api worker web

infra-up: ## Start data plane (Postgres/Qdrant/Valkey/S3/Keycloak) in Docker
	$(COMPOSE) --profile infra up -d
	$(COMPOSE) --profile infra up -d --wait $(INFRA_SERVICES)

infra-down: ## Stop data plane containers (volumes preserved)
	$(COMPOSE) --profile infra down

docker-up: ## Build and start the FULL stack (infra + api + worker + web)
	$(COMPOSE) --profile full up --build -d
	$(COMPOSE) --profile full up -d --wait $(FULL_SERVICES)

docker-down: ## Stop the full stack
	$(COMPOSE) --profile full down

# ----------------------------------------------------------------- database --
db-migrate: ## Run database migrations (alias: migrate)
	uv run alembic -c db/alembic.ini upgrade head

migrate: db-migrate ## Alias for db-migrate

# ---------------------------------------------------------------------- dev --
dev: ## Run API + worker + web locally (infra must be up)
	bash scripts/dev.sh

# ------------------------------------------------------------------ quality --
format: ## Auto-format Python + TypeScript
	uv run ruff format .
	uv run ruff check --fix .
	pnpm run format

lint: ## Lint Python + TypeScript (no writes)
	uv run ruff check .
	uv run ruff format --check .
	pnpm run format:check
	pnpm run -r --if-present lint

typecheck: ## Type-check Python (mypy strict) + TypeScript (tsc)
	uv run mypy
	pnpm run -r --if-present typecheck

# -------------------------------------------------------------------- tests --
test-unit: ## Fast unit tests (no external services)
	uv run pytest -m unit

coverage: ## Unit-test coverage vs gate (fail_under in pyproject)
	uv run pytest -m unit --cov --cov-report=term

test-integration: ## Integration tests (requires `make infra-up` first)
	uv run pytest -m integration

test-architecture: ## Import-boundary + declared-dependency checks
	uv run lint-imports
	uv run python scripts/verify_architecture.py
	uv run python scripts/verify_invariants.py

test-contract: ## API/event/tool contract tests
	uv run pytest -m contract || test $$? -eq 5  # exit 5 = no tests collected yet

test-hooks: ## Coding-agent harness hooks (.claude/hooks) against throwaway repos
	bash scripts/test_claude_hooks.sh

test-e2e: ## End-to-end vertical slice tests (requires full stack)
	uv run pytest -m e2e || test $$? -eq 5

test-web: ## Browser tests for the web app (requires the stack; not in CI)
	# Deliberately outside `test-all` and outside CI: it downloads a browser and
	# wants a running API. Wiring it into the pipeline is a decision about CI
	# runtime, not something a bug fix should make on its way past.
	pnpm --filter @dw/web exec playwright test

check-model: ## Probe the configured LLM gateway (live call, needs OPENAI_* in .env)
	uv run python scripts/check_model_gateway.py

check-rerank: ## Probe the configured reranker (live call, needs DW_API_RERANK_* in .env)
	uv run python scripts/check_rerank.py

check-deepgram: ## Probe Deepgram transcription (live call, needs DEEPGRAM_API_KEY in .env)
	python scripts/check_deepgram.py

check-search: ## Probe the search providers (live calls, needs SERPER/EXA/TAVILY keys in .env)
	python scripts/check_search_providers.py

eval-smoke: ## Evaluation smoke suite (deterministic safety-gate graders)
	uv run python scripts/run_evals.py --smoke

test-eval-smoke: eval-smoke ## Alias for eval-smoke

test-all: test-unit test-architecture test-contract test-integration test-e2e ## Everything except evals

# ---------------------------------------------------------------- contracts --
new-context: ## Scaffold a bounded context wired into all 13 seams (NAME=sales_chat)
	@test -n "$(NAME)" || (echo "usage: make new-context NAME=sales_chat" && exit 1)
	uv run python scripts/new_context.py --name $(NAME)
	uv sync --all-packages

generate-contracts: ## Export OpenAPI snapshot + regenerate TS types (per context)
	uv run python scripts/generate_contracts.py
	pnpm run generate:api-types

release-manifest: ## Generate the immutable release manifest
	uv run python scripts/release_manifest.py

release-manifest-check: ## Verify the committed manifest matches the repo
	uv run python scripts/release_manifest.py --check

# ----------------------------------------------------------------------- ci --
ci: lint typecheck test-unit test-architecture test-contract test-hooks eval-smoke release-manifest-check ## Local CI gate
	@echo ">> local CI gate passed"
