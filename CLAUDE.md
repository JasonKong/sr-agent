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
make db-auditor  # create the agent's audit login (from SRA_AUDIT_DATABASE_URL; insert and read only)
make ingest      # chunk, mask and embed tickets and runbooks into knowledge_chunk (uv run sr-ingest)
make mcp         # run the MCP server over stdio (Claude Code uses .mcp.json; see docs/guides/claude-mcp-setup.md)
make mcp-http    # the MCP server over HTTP on 127.0.0.1:8765, for the agent (needs MCP_HTTP_TOKEN)
uv run sra ask "Why didn't the specialist get GR-11854?"  # the agent; needs make mcp-http and ANTHROPIC_API_KEY
make api         # the agent's HTTP API on 127.0.0.1:8766 for the console (uv run sra serve); uv run sra openapi prints its schema
uv run sim case F07_CLINIC_PMS_AUTH_FAILED  # one labelled case with its evidence ids (no DB needed)
uv run sim case F00_NO_FAULT --distractor recovered_pms_auth  # a case with a distractor
uv run sr-evals build          # rebuild the frozen eval dataset (packages/evals/datasets/v1.json)
uv run sr-evals run --cases smoke   # eval: 12 cases (or all, or ids); needs make seed, make mcp-http and a key
uv run sr-evals run --dry-run       # the same pipeline with an oracle model: free, must score 100%
uv run sr-evals retrieval           # retrieval experiments (embedders x chunk sizes x modes) on scratch DBs
uv run sr-evals serve-oracle        # the agent API with an oracle model, for the console and e2e without a key
make test-integration          # tests marked integration, against the local Postgres
uv run pytest -m live          # tests that call a real model API (skipped by default)
```

CI (`.github/workflows/ci.yml`) runs `make check`, the web console's check, build and Playwright tests (with the oracle), and, against a Postgres service container, `make migrate` and `make test-integration` on every pull request. `.github/workflows/eval.yml` runs the eval smoke set on a manual trigger or a push to `main` (never on pull requests), using the `ANTHROPIC_API_KEY` secret. Actions are pinned to commit SHAs; keep the `# vX.Y.Z` comment next to each SHA. Run `actionlint` after editing workflows.

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
- `packages/web` (TypeScript, pnpm; not in the uv workspace) is the CS console: `make web-install`, `make web-check`, `make web-dev`, `make web-e2e` (Playwright with an oracle model; needs `make seed`). Regenerate its API types with `pnpm --dir packages/web api:types` after changing the agent API. `infra/` (AWS CDK) arrives only in the optional M7.

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
- New tables need an explicit `GRANT SELECT ... TO sr_reader` in their migration. Never grant patient name, NHI or date-of-birth columns. Tables in the `audit` schema are the exception: only `sr_auditor` may touch them.
- Schema changes are new files in `packages/db/src/sr_db/migrations/` named `NNNN_lowercase_words.sql`. Never edit a migration that has been applied; the runner rejects it.
- Integration tests create their own throwaway database; never write test data into the development database.
- Logs are structured JSON with `correlation_id`. Patient names, dates of birth and NHI numbers never appear in logs, tool results or model input.
- Unit tests never call a real model or AWS; use `FakeModel` and fake adapters. Mark real-model tests `@pytest.mark.live`.
- The agent (`sr_agent`):
  - Reaches data only through MCP tools.
  - Keeps the conversation append-only. Each reply is appended exactly as received, thinking blocks included. Never edit earlier messages, the system prompt or the tool list during a run.
  - Tests use `FakeModel` and `FakeToolbox` from `sr_agent.testing`. They connect to the real MCP server in-process with `open_toolbox(Client(create_server(fake_deps())))`.
- Add a dependency to the package that uses it (`uv add --package <dist-name> <dep>`), never to the workspace root. The only exception is dev tools.

## Public-repository boundary

This repository is public, and all of its data is synthetic.

- Never add code, schemas, table or column names, error strings, internal tool names, domain names, documents or data from any real organisation's systems. Borrow patterns, never identifiers.
- Use only the fictional names defined in DESIGN.md section 3:
  - systems: Composer and Hub;
  - vendors: Kauri PMS, Harbour PMS, Summit PMS, Tasman Clinic, Ridgeline.
- Never commit secrets or real-looking personal data.
- `test_simulator_public_boundary_unit.py` scans every repo file and all generated text against a hashed denylist of real names. Add words as hashes only, never in plain text.
