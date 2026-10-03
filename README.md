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

🚧 Early development: M0 (foundation). See the [implementation plan](docs/DESIGN.md#10-implementation-plan-for-claude-code).

| Milestone | Scope | State |
| --- | --- | --- |
| M0 | Workspace, tooling, CI | In progress |
| M1 | Simulator: two systems, four legs, failure scenarios | — |
| M2 | MCP server | — |
| M3 | Baseline agent and evaluation | — |
| M4 | RAG over tickets and runbooks | — |
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
make seed              # 2000 simulated referrals across both systems
make test-integration
```

## Evaluation results

No results yet; the first numbers arrive with M3.

## Documentation

- [Design document](docs/DESIGN.md)
- [Architecture decision records](docs/adr/README.md)

All people, practices, clinics and vendors in this repository are fictional.
