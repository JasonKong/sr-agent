# Architecture decision records

Each significant decision gets one short record. A record is never edited after it is accepted, except to change its status. To change a decision, write a new record that supersedes the old one.

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-python-first-typescript-for-web.md) | Python for the backend, TypeScript for the web console | Accepted |
| [0002](0002-local-first-runtime.md) | Local-first runtime; AWS is optional | Accepted |
| [0003](0003-static-bearer-token-for-mcp-http.md) | A static bearer token for the MCP server's HTTP transport | Accepted |

## Template

```markdown
# NNNN. Title

- Status: Proposed | Accepted | Superseded by NNNN
- Date: YYYY-MM-DD
- Deciders: @Ming

## Context
What problem or force makes this decision necessary.

## Decision
What we will do.

## Alternatives considered
Each option with why it was not chosen.

## Consequences
What becomes easier, what becomes harder, and what would make us revisit this.
```
