"""What each run shows about the agent's machinery (TE3): `sr-evals inspect`.

For every stored run (a report's, or one printed by `sra ask --json`), one row:
- **thinking**: the thinking blocks in the history, and how many have text (summarized
  display). The history is append-only and is what the API was sent, so a run that went
  past its first turn without a 400 replayed every block as received;
- **caching**: the cache reads on each turn. Every turn after the first should read the
  cached prefix; with several cases in a row, the first turn should too (the breakpoint
  after the system prompt);
- **the answer**: a structured answer after tool calls, repair turns, and a wrap-up (a
  limit reached, with a partial answer);
- **who served it**: a turn served by another model than the one asked for is a refusal
  fallback. A dated snapshot of the same model (`claude-haiku-4-5-20251001`) is not;
- turns, tool calls, tokens, cost and time.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from sr_agent.loop import AgentRun
from sr_agent.pricing import canonical_model

_LIMITS = ("tool_limit", "time_limit", "token_limit")


class RunCheck(BaseModel):
    case_id: str
    outcome: str
    turns: int
    tool_calls: int
    tools: list[str]  # in the order called
    thinking_blocks: int
    thinking_with_text: int
    cache_read_by_turn: list[int]
    first_turn_read_cache: bool
    later_turns_read_cache: bool | None  # None for a one-turn run
    structured_with_tools: bool  # an answer fitting the schema, after at least one tool call
    repairs: int
    wrap_up: bool
    served_by: list[str]  # each model once, in order
    fallback: bool
    input_tokens: int  # every input token, cached or not
    output_tokens: int
    cost_usd: float | None
    duration_s: float


def check_run(case_id: str, run: AgentRun, *, requested: str | None = None) -> RunCheck:
    blocks = [
        b
        for m in run.transcript
        if m.get("role") == "assistant" and isinstance(m.get("content"), list)
        for b in m["content"]
    ]
    thoughts = [b for b in blocks if b.get("type") in ("thinking", "redacted_thinking")]
    reads = [t.usage.cache_read_input_tokens for t in run.turn_usage]
    asked = canonical_model(requested or run.model)
    u = run.usage
    return RunCheck(
        case_id=case_id,
        outcome=run.outcome,
        turns=run.turns,
        tool_calls=len(run.tool_calls),
        tools=[c.name for c in run.tool_calls],
        thinking_blocks=len(thoughts),
        thinking_with_text=sum(bool(b.get("thinking")) for b in thoughts),
        cache_read_by_turn=reads,
        first_turn_read_cache=bool(reads and reads[0] > 0),
        later_turns_read_cache=all(r > 0 for r in reads[1:]) if len(reads) > 1 else None,
        structured_with_tools=run.diagnosis is not None and bool(run.tool_calls),
        repairs=run.repairs,
        wrap_up=run.outcome in _LIMITS and run.diagnosis is not None,
        served_by=list(dict.fromkeys(run.served_by)),
        fallback=any(canonical_model(m) != asked for m in run.served_by),
        input_tokens=u.input_tokens + u.cache_creation_input_tokens + u.cache_read_input_tokens,
        output_tokens=u.output_tokens,
        cost_usd=run.cost_usd,
        duration_s=run.duration_ms / 1000,
    )


def _yes(flag: bool | None) -> str:
    return "-" if flag is None else ("yes" if flag else "NO")


def render_checks(checks: Sequence[RunCheck], errors: Mapping[str, str] | None = None) -> str:
    lines = [
        "| Case | Outcome | Turns | Tool calls | Thinking (with text) | Cache read, turn 1 "
        "| Later turns read cache | Repairs | Wrap-up | Served by | Tokens in / out | Cost "
        "| Seconds |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in checks:
        cost = "-" if c.cost_usd is None else f"${c.cost_usd:.3f}"
        first = c.cache_read_by_turn[0] if c.cache_read_by_turn else 0
        lines.append(
            f"| {c.case_id} | {c.outcome} | {c.turns} | {c.tool_calls} | "
            f"{c.thinking_blocks} ({c.thinking_with_text}) | {first:,} "
            f"| {_yes(c.later_turns_read_cache)} | {c.repairs} | {_yes(c.wrap_up)} | "
            f"{', '.join(c.served_by)}{' (fallback)' if c.fallback else ''} | "
            f"{c.input_tokens:,} / {c.output_tokens:,} | {cost} | {c.duration_s:.1f} |"
        )
    multi = [c for c in checks if c.later_turns_read_cache is not None]
    costs = [c.cost_usd for c in checks]
    total = "-" if any(x is None for x in costs) else f"${sum(x or 0 for x in costs):.3f}"
    lines += [
        "",
        f"Every later turn read the cache: {sum(bool(c.later_turns_read_cache) for c in multi)}"
        f"/{len(multi)} runs. First turn read the cache: "
        f"{sum(c.first_turn_read_cache for c in checks)}/{len(checks)}.",
        f"Structured answer after tool calls: {sum(c.structured_with_tools for c in checks)}"
        f"/{len(checks)}. Repairs: {sum(c.repairs for c in checks)}. Wrap-ups: "
        f"{sum(c.wrap_up for c in checks)}. Runs with a fallback turn: "
        f"{sum(c.fallback for c in checks)}.",
        f"Thinking blocks: {sum(c.thinking_blocks for c in checks)}, "
        f"{sum(c.thinking_with_text for c in checks)} with text. Total cost: {total}.",
    ]
    for case_id, error in (errors or {}).items():
        lines.append(f"- {case_id}: {error}")
    return "\n".join(lines) + "\n"
