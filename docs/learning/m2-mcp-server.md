# M2 — MCP server: learning note

> Draft by Claude, from the M2 work. The section "What happened when Claude used it" is yours to fill in after trying the server in Claude Code.

Milestone M2 · Tasks T2.1–T2.6 · PRs #12–#17 · October 2026

## What I built

A read-only MCP server that lets any MCP client (Claude Code, Claude Desktop, and the M3 agent) investigate a referral across both systems. It never sees a patient's name, NHI or date of birth.

| Task | Result |
| --- | --- |
| T2.1 Skeleton | MCP SDK 2.x server; adapter interfaces with fakes; a read-only database role; masking; an output guard; leak tests every tool must join |
| T2.2 Referral tools | `get_referral` (both systems linked), `find_referrals`, `get_referral_timeline` |
| T2.3 Leg tools | `get_deliveries` (every attempt), `get_recipient` (integration health), `get_return_path` |
| T2.4 Logs and overview | An in-memory JSONL log index, `search_logs`, `list_failed_referrals` |
| T2.5 Claude | Runbook resources, the `diagnose_referral` prompt, `.mcp.json`, a setup guide, the evidence reachability test |
| T2.6 HTTP | Stateless Streamable HTTP with a bearer token (ADR 0003) |

In numbers:
- **Code:** about 2,100 lines of server code and 2,200 lines of tests.
- **Tests:** 98 unit and 54 integration tests for the MCP server; 454 unit and 158 integration tests across the repo.

## The MCP primitives, as they ended up

- **Tools** are for facts the model asks for, one leg at a time.
  - **One linked view of a referral, then a tool per leg.** Following the link from Composer to Hub is mechanical, so `get_referral` does it once. Where to look next is diagnosis, so it stays with the model.
  - **Descriptions are written for the model:** when to use the tool, what it returns, and what to call next. They also say what a tool will *not* show. For example, `list_failed_referrals` says silent problems never appear in it.
- **Resources** are reference material the user attaches: the runbooks.
- **Prompts** are a method the user starts: `diagnose_referral` is the same method the agent will use.

## What I learned

### Check that a result is complete, not just correct

The first test of `with_errors` checked that every flagged referral really had an error. A mutation that ignored failed deliveries still passed it, because a smaller list is still a correct one. Only the opposite test caught it: F07 and F09 must be flagged, while F06 and F10 must not.

**For filters and searches, test what must be found, not only what is found.**

### Test that the evidence can be reached at all

The reachability test asks, for every failure code and distractor kind, whether each record a correct diagnosis should cite appears in *some* tool's output. On its first run it found that F04's hand-off attempts were in none. No prompt could ever have made the model cite them.

This is a property of the toolset, not of any single tool. Contract tests, which check one tool at a time, would never have found it.

### Contract tests against a simulator

Every SQL query is compared field by field with the simulator's ground truth, for every failure code and distractor kind. Wrong joins, wrong ordering and missing rows show up at once. A seeded simulator turns integration tests into exact comparisons rather than "it returned something".

### Privacy enforced by the database, in layers

1. **Database.** The server's role cannot `SELECT` patient name, NHI or date-of-birth columns at all. A `SECURITY DEFINER` function returns only `present`, `empty` or `invalid`.
2. **Adapter.** Tools get a stable `PT-` reference (an HMAC), never a value.
3. **Output guard.** Every result is scrubbed for NHI patterns and day-first dates, counting each redaction.
4. **Leak tests.** Every registered tool must be listed, and is called on seeded cases through the real read-only role.

A mutation that granted the full table to the role failed five tests. Code can have bugs; a permission the database never grants cannot leak.

### Read the SDK before designing

The MCP Python SDK had moved to 2.x (`MCPServer`, an in-memory `Client(server)`), and my memory of 1.x was out of date. Probing it in a scratch environment first meant the design used real APIs. That included testing every tool through the actual protocol, in memory, with no subprocess.

### Interfaces and fakes

`ReferralStore`, `LogSource` and `KnowledgeSearch` let the tool layer be unit-tested with fakes, and the SQL be tested separately. A real organisation could swap in its own data sources. The JSONL log source will become CloudWatch later without the tools changing.

### Small things that would have bitten later

- **Stdout belongs to the stdio protocol.** All logging goes to stderr, and a test checks stdout stays empty.
- **Don't rely on the working directory.** Claude clients don't promise one, so paths resolve from `CLAUDE_PROJECT_DIR` or the uv workspace. A test starts the server from another directory.
- **"Now" is the latest time in the data,** not the wall clock. Simulated data stops at its anchor time.
- **One time zone everywhere (UTC).** Mixing zones invites wrong before-and-after reasoning.
- **Bearer tokens.** The scheme is case-insensitive (RFC 7235), the token is compared in constant time, and the token is never logged. A health check is the only open path.
- **Empty is not an answer.** No deliveries always comes with a reason: no Hub record, not accepted yet, or routed `MANUAL`. Unknown ids are a typed `NOT_FOUND`, never an empty success.

## What I measured

| Measure | Value |
| --- | --- |
| Tools / resources / prompts | 8 / 17 runbooks plus an index / 1 |
| MCP tests | 98 unit, 54 integration (about 25 s) |
| Mutation checks | 16 deliberate bugs (T2.1, T2.2 and T2.3: 3 each; T2.4: 4; T2.6: 3), each caught by the intended test |
| Log index | about 37k lines in memory; reloads when the files change |
| HTTP auth | 7 wrong `Authorization` forms refused; the token appears 0 times in logs |

## What happened when Claude used it

`TODO(me)`: approve `sr-agent` in a new Claude Code session, ask the five questions in `docs/guides/claude-mcp-setup.md`, and note for each:

| Case | Right root cause? | Tools it called, in order | Evidence it cited | Fell for a trap? | Notes (confusing descriptions, wasted calls) |
| --- | --- | --- | --- | --- | --- |
| GR-11854 (F07) | | | | | |
| GR-10008 (F11) | | | | | |
| GR-10080 (F06) | | | | | |
| GR-10076 (401, then recovered) | | | | | |
| GR-11965 (injection in a note) | | | | | |

## Open questions for M3

- **The answer schema.** How strict should it be? Evidence as structured `record_type` and `record_id` (DESIGN.md section 7.4) makes scoring exact, but asks more of the model.
- **Clinic-level faults cluster in time.** They always hit each clinic's most recent referrals, so recent failures are mostly delivery failures. Does that make evaluation too easy? Should the evaluation set balance it?
- **Tool budget.** A thorough check of one referral takes 6 calls (the reachability test's sequence). Is 8 calls enough once logs and knowledge are added?
- **Cost.** Which Claude model gives the best accuracy for its cost? This is the first real measurement in M3.

## Interview talking points

- "I tested the toolset, not just each tool: a reachability test checks that every piece of evidence a correct diagnosis needs is in some tool's output. Its first run found evidence no prompt could have reached."
- "Patient privacy is enforced by the database. The MCP server's role can't select those columns, so a bug can't leak them, and leak tests prove it for every tool."
- "For search and filter tools I test completeness as well as correctness. A test that only checks 'every result is right' passes when results go missing."
