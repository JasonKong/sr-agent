# CLAUDE.md

SR-Agent is a learning-first, local-first AI support agent that explains why a simulated GP-to-specialist referral failed. **`docs/DESIGN.md` is the source of truth**; read the sections a task names before changing code. Decisions and their reasons live in `docs/adr/`.

## Commands

```bash
make install     # uv sync: all workspace packages and dev tools
make check       # ruff check, ruff format --check, mypy (strict), pytest — must pass before a task is done
make fmt         # ruff format + ruff check --fix
make db-up       # start Postgres 16 + pgvector (needs .env: cp .env.example .env)
make migrate     # apply SQL migrations (uv run sr-db migrate); make db-reset wipes local data
make seed        # replace simulator data with the default world (uv run sim seed --help)
make db-roles    # create the MCP server's read-only login (from MCP_DATABASE_URL)
make mcp         # run the MCP server over stdio (Claude Code uses .mcp.json; see docs/guides/claude-mcp-setup.md)
uv run sim case F07_CLINIC_PMS_AUTH_FAILED  # one labelled case with its evidence ids (no DB needed)
uv run sim case F00_NO_FAULT --distractor recovered_pms_auth  # a case with a distractor
make test-integration          # tests marked integration, against the local Postgres
uv run pytest -m live          # tests that call a real model API (skipped by default)
```

CI (`.github/workflows/ci.yml`) runs `make check` and, against a Postgres service container, `make migrate` and `make test-integration` on every pull request. Actions are pinned to commit SHAs; keep the `# vX.Y.Z` comment next to each SHA. Run `actionlint` after editing workflows.

## Layout

- uv workspace; members are listed explicitly in the root `pyproject.toml`.
- Python packages live in `packages/<dir>/src/<import_name>`:
  - `db` → `sr_db` (settings, migration runner, all SQL migrations)
  - `simulator` → `sr_simulator`
  - `mcp_server` → `sr_mcp`
  - `agent` → `sr_agent`
  - `evals` → `sr_evals`
- Tests live in `packages/<dir>/tests/`. Test file names must be unique across the repo: the tests directories have no `__init__.py`, and mypy and pytest import by file name.
- Shared pytest fixtures live in `sr_db.testing` (for example `migrated`, a throwaway migrated database), loaded by the root `conftest.py`. Do not add another `conftest.py`.
- `packages/web` (TypeScript) arrives in M6; `infra/` (AWS CDK) only in the optional M7.

## Working on a task

- Implement one task (`T<id>` in DESIGN.md section 10) per branch and pull request. Use Conventional Commits.
- First list the files you plan to create or change, and wait for approval.
- Write failing tests first, then the code. Finish by running `make check` until it is green.
- Summarise what changed, any decision that needs an ADR or a DESIGN.md update, and one point for the milestone's learning note in `docs/learning/`.
- When a decision changes, update `docs/DESIGN.md` in the same pull request.

## Conventions

- Python 3.12, type hints everywhere, mypy strict. Validate data at boundaries with Pydantic; treat model output as untrusted input.
- Configuration comes from environment variables, validated at start-up. `.env` is git-ignored and local-only.
- Every data source goes through an adapter interface, never called directly from tool or agent code.
- Knowledge: ticket phrase banks and styles in `knowledge/ticket_phrases/` and `knowledge/ticket_styles.yaml`; runbooks in `knowledge/runbooks/*.md`. Keep runbooks in step with the simulator; a test checks each names its scenario's evidence.
- Distractors: one YAML per kind in `distractor_specs/`, mechanics in `distractors.py`; they never change failure labels.
- Failure scenarios: one YAML per failure code in `packages/simulator/src/sr_simulator/scenarios/`, with the mechanics in `faults.py`. Ground-truth labels go to `var/sim/labels.json`, never to the database.
- MCP tools (MCP SDK 2.x, `MCPServer`):
  - Register every tool with `@guarded_tool(server, deps)` in a group listed in `TOOL_GROUPS`.
  - Return a `ToolResult` subclass.
  - Raise `NotFound` for unknown ids.
  - Add the tool to `TOOL_INPUTS` in `test_mcp_leaks_integration.py`, or the leak tests fail.
  - Never print to stdout in the server; stdout is the stdio protocol.
  - Never rely on the working directory; resolve paths with `sr_mcp.paths.project_root()`.
  - If a scenario's evidence cannot be reached through the tools, `test_mcp_evidence_reachable_integration.py` fails.
- New tables need an explicit `GRANT SELECT ... TO sr_reader` in their migration. Never grant patient name, NHI or date-of-birth columns.
- Schema changes are new files in `packages/db/src/sr_db/migrations/` named `NNNN_lowercase_words.sql`. Never edit a migration that has been applied; the runner rejects it.
- Integration tests create their own throwaway database; never write test data into the development database.
- Logs are structured JSON with `correlation_id`. Patient names, dates of birth and NHI numbers never appear in logs, tool results or model input.
- Unit tests never call a real model or AWS; use `FakeModel` and fake adapters. Mark real-model tests `@pytest.mark.live`.
- Add a dependency to the package that uses it (`uv add --package <dist-name> <dep>`), never to the workspace root. The only exception is dev tools.

## Public-repository boundary

This repository is public, and all of its data is synthetic.

- Never add code, schemas, table or column names, error strings, internal tool names, domain names, documents or data from any real organisation's systems. Borrow patterns, never identifiers.
- Use only the fictional names defined in DESIGN.md section 3:
  - systems: Composer and Hub;
  - vendors: Kauri PMS, Harbour PMS, Summit PMS, Tasman Clinic, Ridgeline.
- Never commit secrets or real-looking personal data.
- `test_simulator_public_boundary_unit.py` scans every repo file and all generated text against a hashed denylist of real names. Add words as hashes only, never in plain text.
