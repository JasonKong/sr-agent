.PHONY: install check lint typecheck test fmt db-up db-down db-reset migrate db-roles db-auditor seed ingest mcp mcp-http api web-install web-check web-dev web-e2e test-integration

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

mcp-http: ## Run the MCP server over HTTP on 127.0.0.1:8765 (bearer token: MCP_HTTP_TOKEN)
	uv run sr-mcp --transport http

db-auditor: ## Create the agent's audit login role from SRA_AUDIT_DATABASE_URL
	uv run sr-db create-auditor

api: ## Run the agent's HTTP API on 127.0.0.1:8766 (needs make mcp-http and ANTHROPIC_API_KEY)
	uv run sra serve

seed: ## Replace simulator data with the default generated world (DESIGN.md 3.6)
	uv run sim seed

ingest: ## Chunk, mask and embed tickets and runbooks for retrieval (after make seed)
	uv run sr-ingest

test-integration: ## Tests that need the local Postgres (run make db-up first)
	uv run pytest -m integration

web-install: ## Install the web console's packages (pnpm)
	pnpm --dir packages/web install --frozen-lockfile

web-check: ## The web console's typecheck, lint, format check and unit tests
	pnpm --dir packages/web check

web-dev: ## The web console on http://localhost:5173 (proxies /api to make api)
	pnpm --dir packages/web dev

web-e2e: ## Playwright end-to-end tests with an oracle model (needs make seed; E2E_API=live for the real model)
	pnpm --dir packages/web e2e
