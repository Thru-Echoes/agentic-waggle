# Waggle — local Makefile.
#
# Convention: every target is idempotent. No target swallows errors.

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose

TEST_DB := agentic_mcp_crm_test
TEST_DB_URL := postgresql://agentic_mcp_crm:agentic_mcp_crm@postgres:5432/$(TEST_DB)

.PHONY: help up down build migrate seed reseed embed demo logs psql reset-db check \
        test test-unit test-db-create test-db-drop

help:
	@echo "Waggle (agentic-mcp-crm) — local launcher"
	@echo ""
	@echo "Core workflow:"
	@echo "  make build             build the app image from ./Dockerfile"
	@echo "  make up                boot Postgres (pgvector) + Redis"
	@echo "  make migrate           run local alembic migrations"
	@echo "  make seed              generate synthetic beekeeping data"
	@echo "  make demo              start the Waggle api (http://localhost:5001)"
	@echo "  make down              tear everything down (preserves volumes)"
	@echo ""
	@echo "Tests:"
	@echo "  make test              full E2E suite (creates test DB if missing)"
	@echo "  make test-unit         only non-DB tests (SKIP_DB_TESTS=1)"
	@echo "  make test-db-create    CREATE DATABASE $(TEST_DB) if missing"
	@echo "  make test-db-drop      DROP DATABASE $(TEST_DB)"
	@echo ""
	@echo "Maintenance:"
	@echo "  make reseed            drop data and regenerate"
	@echo "  make reset-db          drop DB entirely and rerun migrate+seed"
	@echo "  make embed             regenerate inspection embeddings only"
	@echo "  make psql              open a psql prompt against the local DB"
	@echo "  make logs              tail compose logs"
	@echo "  make check             verify prerequisites"

check:
	@command -v docker >/dev/null 2>&1 || { echo "docker not found"; exit 1; }
	@$(COMPOSE) version >/dev/null 2>&1 || { echo "docker compose not available"; exit 1; }
	@echo "OK: prerequisites present"

build: check
	$(COMPOSE) build

up: check
	$(COMPOSE) up -d postgres redis
	@echo "Waiting for Postgres to accept connections..."
	@until $(COMPOSE) exec -T postgres pg_isready -U agentic_mcp_crm -d agentic_mcp_crm >/dev/null 2>&1; do sleep 1; done
	@echo "Postgres ready"

migrate: up
	$(COMPOSE) run --rm seed alembic upgrade head

seed: migrate
	$(COMPOSE) run --rm seed python -m seed.generate

embed:
	$(COMPOSE) run --rm seed python -m seed.embeddings

reseed:
	$(COMPOSE) run --rm seed python -m seed.generate --drop-existing

reset-db:
	$(COMPOSE) down -v
	$(MAKE) up
	$(MAKE) migrate
	$(MAKE) seed

demo: up
	@echo "Starting Waggle api..."
	$(COMPOSE) up -d api
	@echo ""
	@echo "Waggle:  http://localhost:5001"

logs:
	$(COMPOSE) logs -f --tail=100

psql:
	$(COMPOSE) exec postgres psql -U agentic_mcp_crm -d agentic_mcp_crm

down:
	$(COMPOSE) down

# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------

test-db-create: up
	@echo "Ensuring $(TEST_DB) exists..."
	@$(COMPOSE) exec -T postgres psql -U agentic_mcp_crm -d postgres -tAc \
		"SELECT 1 FROM pg_database WHERE datname='$(TEST_DB)'" | grep -q 1 || \
	$(COMPOSE) exec -T postgres psql -U agentic_mcp_crm -d postgres \
		-c "CREATE DATABASE $(TEST_DB)"
	@echo "Test database ready: $(TEST_DB)"

test-db-drop: up
	@$(COMPOSE) exec -T postgres psql -U agentic_mcp_crm -d postgres \
		-c "DROP DATABASE IF EXISTS $(TEST_DB)"

# Full E2E test suite. Creates the test DB if missing, runs pytest with both
# TEST_DATABASE_URL (used by the `db` fixture) and DATABASE_URL (used by
# providers/MCP tools when they open their own sessions) pointed at the test
# DB, so the whole test run stays on one isolated schema. No silent skips —
# missing deps fail.
test: test-db-create
	$(COMPOSE) run --rm \
		-e TEST_DATABASE_URL=$(TEST_DB_URL) \
		-e DATABASE_URL=$(TEST_DB_URL) \
		-e ANTHROPIC_API_KEY=$${ANTHROPIC_API_KEY:-} \
		-e OPENAI_API_KEY=$${OPENAI_API_KEY:-} \
		seed pytest tests/ -vv --tb=short

# Only tests that do not require a DB.
test-unit:
	$(COMPOSE) run --rm \
		-e SKIP_DB_TESTS=1 \
		seed pytest tests/test_seed_models.py -vv
