.PHONY: install check lint typecheck test fmt

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
