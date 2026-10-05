# SR-Agent

[![CI](https://github.com/JasonKong/sr-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/JasonKong/sr-agent/actions/workflows/ci.yml)

An AI support agent that tells a client-service team member, in plain English and within seconds, why a GP-to-specialist referral failed or was never received — with the evidence to prove it.

> "The specialist at Kowhai Orthopaedics says they never got GR-10234. Why?"
>
> **Root cause:** the clinic's PMS connection expired, so the Hub could not push the referral into their system. Email and portal delivery succeeded.
> **Evidence:** clinic PMS delivery failed with no attempts recorded; integration auth status `EXPIRED`; Hub worker log "auth failed: token expired".
> **Next step:** ask the clinic to re-authorise its PMS connection, then resend.

SR-Agent is a learning project for **MCP**, **RAG** and **agents**. It runs entirely on simulated data: two cooperating referral systems with labelled failure scenarios. Every change is measured against an evaluation set.

## Status

🚧 M0–M3 code is complete: the simulated world, the MCP server (10 read-only tools including hybrid knowledge search, runbook resources, a diagnosis prompt, stdio and HTTP), the agent (`sra ask`) and the evaluation harness (`sr-evals`). M4 (RAG) is in progress; the first model-graded numbers need an API key. See the [implementation plan](docs/DESIGN.md#10-implementation-plan-for-claude-code).

| Milestone | Scope | State |
| --- | --- | --- |
| M0 | Workspace, tooling, CI | ✅ Done |
| M1 | Simulator: two systems, four legs, failures, distractors, tickets and runbooks | ✅ Done |
| M2 | MCP server | ✅ Done |
| M3 | Baseline agent and evaluation | 🚧 Code done; baseline run pending |
| M4 | RAG over tickets and runbooks | 🚧 In progress |
| M5 | Agent hardening | — |
| M6 | CS web console | — |
| M7 | Optional AWS deployment | — |

## How it works

- **MCP server:** read-only, privacy-safe tools over both systems' data and logs.
- **RAG:** hybrid search over past support tickets and runbooks.
- **Agent:** a tool-use loop that gathers evidence and returns a validated diagnosis.

Details: [docs/DESIGN.md](docs/DESIGN.md).

## Quick start

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 if needed) and Docker.

```bash
cp .env.example .env   # local-only settings
make install
make check             # lint, type checks, unit tests
make db-up             # Postgres 16 + pgvector
make migrate
make db-roles           # read-only login for the MCP server
make seed              # 2000 referrals across both systems, logs, tickets, runbooks
make ingest            # chunk and embed tickets and runbooks (downloads a local model once)
uv run sim case F07_CLINIC_PMS_AUTH_FAILED   # one labelled case and its evidence
make test-integration
```

## Try it with Claude

After the quick start, open the project in Claude Code, approve the `sr-agent` MCP server, and ask: *"The specialist says GR-11854 never arrived in their system. Why?"* See [the setup guide](docs/guides/claude-mcp-setup.md) for Claude Desktop and more examples.

Or ask the project's own agent from the terminal (needs `ANTHROPIC_API_KEY` in `.env`):

```bash
make mcp-http          # in another terminal
uv run sra ask "The specialist says GR-11854 never arrived in their system. Why?"
```

## Evaluation results

No results yet; the first numbers arrive with M3.

## Documentation

- [Design document](docs/DESIGN.md)
- [Architecture decision records](docs/adr/README.md)
- Learning notes: [M0 foundation](docs/learning/m0-foundation.md), [M1 simulator](docs/learning/m1-simulator.md), [M2 MCP server](docs/learning/m2-mcp-server.md)

All people, practices, clinics and vendors in this repository are fictional.
