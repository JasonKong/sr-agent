.PHONY: install check lint typecheck test fmt db-up db-down db-reset migrate db-roles seed mcp test-integration

install: ## Install all workspace packages and dev tools
	uv sync

check: lint typecheck test ## Everything a task must pass before it is done

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

test:
	uv run pytest

fmt: ## Format and auto-fix lint issues
	uv run ruff format .
	uv run ruff check --fix .

db-up: ## Start Postgres and wait until it is healthy
	docker compose up -d --wait postgres

db-down: ## Stop Postgres, keeping its data
	docker compose down

db-reset: ## Delete all local data and start an empty Postgres
	docker compose down -v
	docker compose up -d --wait postgres

migrate: ## Apply pending SQL migrations
	uv run sr-db migrate

db-roles: ## Create the MCP server's read-only login role from MCP_DATABASE_URL
	uv run sr-db create-reader

mcp: ## Run the MCP server over stdio (what Claude Desktop and Claude Code launch)
	uv run sr-mcp

seed: ## Replace simulator data with the default generated world (DESIGN.md 3.6)
	uv run sim seed

test-integration: ## Tests that need the local Postgres (run make db-up first)
	uv run pytest -m integration
