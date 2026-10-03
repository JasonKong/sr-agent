# M0 — Foundation: learning note

> **Draft by Claude. Rewrite it in your own words.** Lines marked `TODO(me)` are for your own reflections; the rest is a starting point to edit, cut or expand.

Milestone M0 · Tasks T0.1–T0.3 · PRs #1–#4 · October 2026

## What I built

- **Design v0.2 and ADRs 0001–0002.** The project became learning-first and local-first. The simulated domain models two systems (Composer and Hub) and four legs instead of one pipeline. Python became the backend language, and evaluation moved before RAG.
- **T0.1:** a uv workspace with five Python packages, plus Ruff, mypy (strict) and pytest behind one command, `make check`.
- **T0.2:** Postgres 16 with pgvector in Docker, and a shared `sr_db` package with validated settings and a small migration runner.
- **T0.3:** GitHub Actions CI with a unit job and an integration job (Postgres service container), and Dependabot.

## What I learned

### 1. Choosing the language for an LLM application

An LLM application mostly waits on the network and moves JSON around; the model runs remotely. So the language is chosen for its ecosystem and where the code runs, not for speed.

- Python leads on the data and model side: embeddings, evaluation, data generation.
- TypeScript leads on the product side: browsers, serverless, streaming UIs.
- Because components meet only at protocols (MCP, and HTTP with JSON schemas), each choice can be reversed later.
- The rule that holds in every language: validate model output at runtime, because it is untrusted input.

`TODO(me)`: how would I explain this in 30 seconds in an interview?

### 2. A uv workspace is one environment and one lock file for many packages

- The root `pyproject.toml` is a virtual project (`package = false`). It depends on every member, so `uv sync` installs them all as editable packages.
- I listed members explicitly instead of using `packages/*`. The TypeScript web package arriving in M6 will have no `pyproject.toml`, and a glob would break.
- **Surprise:** test files must have unique names across the whole repository. Test folders have no `__init__.py`, so mypy and pytest identify a module by its file name, and two `test_smoke.py` files would collide. A single-package project never hits this.

### 3. Prove that a check can fail before trusting it

- Each smoke test was run before the code existed and failed with `AttributeError`. A test that has never failed has not proven anything.
- I added a deliberately broken file (unused imports, an untyped function) to confirm that Ruff and mypy actually reject it, then removed it.
- A test caught my own bug in T0.2. The test fixture built a `key=value` connection string, but `DATABASE_URL` is validated as a URL. The fix belonged in the fixture, not in loosening the validation.

### 4. Migrations: small, forward-only and strict

- Each file runs in its own transaction, so a broken migration leaves nothing half-applied. A test proves it: the table created by the first half of a failing file does not exist afterwards.
- A checksum per applied file means editing history is an error; every change is a new file.
- A Postgres advisory lock stops two processes from migrating at the same time. It is session-level, so it must be released in a `finally` block.
- **psycopg 3 detail:** on a connection that is *not* in autocommit mode, the first statement opens an implicit transaction. Every later `conn.transaction()` then becomes a savepoint inside it, and nothing is committed per file. The runner therefore insists on an autocommit connection.
- I chose a hand-written runner over Alembic: SQL-first schemas, no ORM, and about 100 lines I can read end to end.

`TODO(me)`: would I make the same choice in a team project? When does Alembic win?

### 5. CI hardening for a public repository

- **Least privilege:** `permissions: contents: read`, no secrets, and `persist-credentials: false`. A pull request from a fork can run CI without being able to leak or misuse anything.
- **Pin actions to commit SHAs, not tags.** A tag like `@v7` can be moved to a different commit. A compromised action can then run in every repository that uses that tag, which has happened in real supply-chain incidents. Dependabot keeps the pinned SHAs up to date.
- **`UV_LOCKED=1`** makes CI fail when `uv.lock` does not match the `pyproject.toml` files.
- I check the whole workspace on every run. Path filtering would save seconds but could miss cross-package effects.

### 6. Git and GitHub identity

- The **push identity** (the SSH key selected by the `github.com-jasonkong` alias) and the **commit author** (`user.email`) are configured separately. I set this repository's email locally and left the global configuration alone.
- I rewrote the published initial commit with `--force-with-lease`. It refuses to push if the remote has moved since I last saw it, unlike a plain `--force`.
- `gh` can hold several accounts. Setting `GH_TOKEN=$(gh auth token --user JasonKong)` for a single command avoids switching the active account that other projects rely on.
- **Stacked PRs:** CI triggers on every `pull_request`, whatever the base branch. Merge them in order with a merge commit or "rebase and merge"; squashing rewrites commits and forces the later PRs to be rebased.

## What I measured

| Measure | Value |
| --- | --- |
| Unit tests | 20, about 0.4 s locally |
| Integration tests | 5, about 0.7 s locally, each in a throwaway database |
| CI duration (check / integration) | 14 s / 24 s on the first run of PR #4 |
| Postgres / pgvector versions | 16.15 / 0.8.7 |

## Open questions for later milestones

- Integration tests share one Postgres server. Will per-test databases stay fast once the M1 schemas and seed data exist, or will the tests need a template database?
- Does a second `conftest.py` collide in mypy, the same way duplicate test file names do? This will come up when the second package needs shared fixtures.
- `TODO(me)`

## Interview talking points

- `TODO(me)`: pick two or three items above and practise saying them in under a minute each. Suggestions: SHA pinning, transaction-per-migration with a test that proves rollback, and language choice in LLM apps.
