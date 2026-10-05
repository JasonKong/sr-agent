# M3 — Baseline agent and evaluation: learning note

> Draft by Claude, from the M3 work. Sections marked **PENDING** need a run against the model API (`ANTHROPIC_API_KEY` in `.env`); the commands to fill them are given in place.

Milestone M3 · Tasks T3.1–T3.4 · October 2026

## What I built

| Task | Result |
| --- | --- |
| T3.1 Loop | `ModelClient` (Anthropic, Fake); a hand-written async tool-use loop that is an MCP client over HTTP; `sra ask` |
| T3.2 Answer | A structured answer (structured outputs), evidence checked against what the run saw, one repair turn, a wrap-up turn at every limit |
| T3.3 Dataset | 75 frozen cases from the default world; deterministic scorers against the simulator's ground truth |
| T3.4 Runner | `sr-evals run` with reports, run-to-run comparison and cost; an oracle dry run; the CI smoke set |

In numbers:
- **Code:** about 2,750 lines in `sr_agent` and `sr_evals`, and 2,300 lines of tests.
- **Tests:** 96 unit and 1 integration test for the agent; 36 unit and 4 integration tests for the evals.
- **Mutation checks:** 29 deliberate bugs (T3.1: 7, T3.2: 7, T3.3: 7, T3.4: 8), each caught by a test. Three needed a new or sharper test first: ids in tool errors (below), and two dataset rules that only the frozen-file comparison had caught, which fails on any change and so says nothing about which rule broke.

## How the loop works, in one paragraph

The question, the system prompt and the tool definitions go to the model. If the reply asks for tools, the loop calls them on the MCP server, all at once, and sends every result back in one message. It goes round again until the reply is an answer. The answer must fit a JSON schema, and every record it cites must have appeared in a tool result during the run. One repair turn is allowed. At eight tool calls, 60 seconds or the token budget, one last turn without tools asks for the best answer so far, marked incomplete.

## What I learned

### What a framework hides

- **History is append-only.** Every reply goes back exactly as received, thinking blocks included, and nothing earlier is ever edited. The API rejects an edited history that carries thinking blocks, and an unchanged prefix is what prompt caching reuses. So the transcript is kept in the provider's own format: a "clean" provider-neutral format would have dropped the thinking blocks.
- **Several tool calls get one reply message.** Splitting their results into separate messages teaches the model to stop calling tools in parallel.
- **Every `tool_use` needs a `tool_result`, even one the loop refuses to run.** At a limit, the wrap-up turn answers pending calls with an `is_error` "not run" result.
- **Timeouts need a wall clock outside the SDK.** The SDK's timeout applies per attempt, so with retries a call can take three times as long. The loop wraps every model and tool call in `asyncio.timeout(time left)`.
- **Model-specific rules change the loop.** On Claude Opus 5.5:
  - thinking is always on, and effort is the control;
  - forcing a tool call is rejected, so the wrap-up uses `tool_choice: none`;
  - a refusal must be handled before the content is read.

  I read the current API guidance first, as with the MCP SDK in M2.

### Check evidence against what the run saw, not against the database

The model writes only `record_id` and `detail` for each piece of evidence; the record type and system come from the id's prefix. An id resolves only if it appeared in a successful tool result in this run. That is stronger than "the record exists": the model may cite only what it looked at.

**A mutation found a gap.** Ids that appear in a tool *error* (`NOT_FOUND: no referral with id GR-99999`) were counted as seen, and no test caught it. Now a test does.

### Test the pipeline with an agent that knows the answers

`sr-evals run --dry-run` replaces the model with an oracle.

- **It reads the answer key,** calls the same six tools a careful agent would, and cites the expected evidence.
- **Through the real database, HTTP server, loop, answer checks and scorers, it must score 100%.** It does, on all 75 cases, in two seconds.
- **Anything less would be a disagreement** between the dataset, the tools and the scorers: a bug in the measuring instrument, found before any model is blamed for it.

### A dataset is code: freeze it, test it, read it

- **Frozen:** the dataset is a file. A test rebuilds it from the default world, so a simulator change cannot silently change what is measured.
- **Read it as a user would.** Reading the generated questions found two problems no test had asked about.
  - *A leading question.* F00 questions read "Did the specialist get it? … Can you find out why?", which presupposes a fault in exactly the cases that test whether the agent invents one.
  - *An inflated evidence list.* One case listed 21 expected records, 13 of them past tickets, which would have swamped "key evidence recall". Tickets are context, not proof, so they no longer count.
- **Solvable by construction.** A question that names a referral only by practice, GP, clinic and day is used only when exactly one referral fits. An integration test finds each one through `find_referrals`.

### Small things that would have bitten later

- **Exception groups.** Anything raised inside an MCP `Client` context, even by the caller, comes out wrapped in nested exception groups. The toolbox unwraps them: transport failures become a clear `ToolsUnavailable` (a 401 is detected with an HTTP response hook), and the caller's own errors come out unchanged.
- **Missing credentials.** With no API key, the SDK fails only when it builds the first request, with a plain `TypeError`, after the agent has already connected to MCP. A real run found this; `sra` now checks before connecting.
- **Settings read `.env` without exporting it.** The SDK cannot see a key that only pydantic-settings has read, so the key is passed explicitly.
- **Tool results as compact JSON** save tokens on every call: the server's text content is indented.

## What I measured

| Measure | Value |
| --- | --- |
| Dataset | 75 cases: 60 clean (5 per code), 15 distractors; named by Composer id 46, Hub id 17, practice and day 12 |
| Oracle dry run (pipeline check) | 100% on every measure, 75 cases, 2 s |
| Baseline accuracy without RAG | **PENDING**: `uv run sr-evals run --no-rag --label baseline` |
| Per code, distractors, failures | **PENDING**: from the same report |
| Latency p50 / p95, tool calls, cost per case | **PENDING**: from the same report. A rough estimate before the run: $0.15 to $0.30 per case on Claude Opus 5.5 |

## The cases it fails, and why

**PENDING.** After the baseline run, read the "Wrong or incomplete" table and each case's transcript in `var/evals/runs/<report id>/`.

- **Look out for:**
  - F10 against F00 (`in_progress`): the 7-day threshold is in the runbook, not the prompt;
  - recovered errors read as failures;
  - questions that name only the practice and day.
- **Things to note:** which tools it skipped, whether it called `get_recipient` before blaming the clinic, and how often the repair turn was needed.

## Open questions for M4

- **Does RAG help, or does it import the traps?** Retrieval itself reproduces the distractor trap: the four retrieval misses are all "it recovered" queries that retrieve the failure they resemble (T4.3).
- **How many tool calls does a knowledge search cost** against the eight-call budget?
- **Cost:** which model is good enough? The `--model` ablation answers it, and ADR 0004 records the default.

## Interview talking points

- "I built the agent loop by hand to see what frameworks hide: append-only history for thinking blocks and caching, one reply message for parallel tool results, a wall-clock limit around SDK retries, and a wrap-up turn so a limit still gives a partial answer."
- "The agent may only cite records it actually saw in this run. That is checked in code, with one repair turn, and evidence that still does not resolve is dropped and counted."
- "Before measuring the model, I measured the measuring instrument: an oracle that knows the answers must score 100% through the real tools and scorers, and it does."
