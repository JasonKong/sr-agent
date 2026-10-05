"""The diagnosis loop: a hand-written tool-use loop (DESIGN.md section 7.4).

Each turn sends the whole conversation to the model. If it asks for tools, the loop calls
them through the toolbox, appends the results as one user message, and goes round again.
It stops when the model answers, or when a limit is reached.

- **The history is append-only.** Every reply is appended exactly as received, thinking
  blocks included, and nothing earlier is ever edited. The model API rejects edited
  histories that carry thinking blocks, and an unchanged prefix is what prompt caching
  reuses.
- **Limits belong to the loop**, because they are a policy for one diagnosis whatever the
  model provider or transport: tool calls, wall-clock time and tokens. A batch of tool calls
  that would go over the budget is not run at all. Every model and tool await runs under
  `asyncio.timeout` with the time left, so retries inside the SDK cannot overrun it.
- **Failures of the services themselves** (the model API, the MCP server) are raised, not
  reported as an outcome: they say nothing about the referral.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Literal

from pydantic import BaseModel, computed_field

from sr_agent.model import Block, Message, ModelClient, ModelRequest, TokenUsage, ToolUse
from sr_agent.prompts import PROMPT_VERSION, build_system_prompt
from sr_agent.toolbox import Toolbox

Outcome = Literal[
    "answered",
    "tool_limit",
    "time_limit",
    "token_limit",
    "max_tokens",  # the reply hit its own output limit
    "refused",
    "no_answer",  # the model ended its turn without any text
    "unexpected_stop",  # a stop reason or reply the loop cannot act on
]


@dataclass(frozen=True)
class Limits:
    max_tool_calls: int = 8
    time_limit_s: float = 60.0
    token_budget: int = 200_000  # every token processed in the run, cached or not
    max_tokens_per_reply: int = 16_000


DEFAULT_LIMITS = Limits()


class ToolCallRecord(BaseModel):
    turn: int
    name: str
    arguments: dict[str, Any]
    is_error: bool
    result_chars: int
    duration_ms: float


class AgentRun(BaseModel):
    run_id: str
    question: str
    answer: str | None
    outcome: Outcome
    model: str  # requested
    served_by: list[str]  # the model that served each turn
    prompt_version: str
    turns: int
    tool_calls: list[ToolCallRecord]
    usage: TokenUsage
    duration_ms: float
    transcript: list[Message]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def incomplete(self) -> bool:
        return self.outcome != "answered"


@dataclass(frozen=True)
class RunStarted:
    run_id: str
    model: str
    limits: Limits


@dataclass(frozen=True)
class ModelCalled:
    run_id: str
    turn: int
    stop_reason: str | None
    model: str
    usage: TokenUsage
    duration_ms: float


@dataclass(frozen=True)
class ToolCalled:
    run_id: str
    record: ToolCallRecord


@dataclass(frozen=True)
class RunFinished:
    run_id: str
    run: AgentRun


AgentEvent = RunStarted | ModelCalled | ToolCalled | RunFinished
Observer = Callable[[AgentEvent], None]


def json_log(event: AgentEvent) -> None:
    """One JSON line on stderr per event, with the run id as correlation id, never content."""
    record: dict[str, Any] = {"correlation_id": event.run_id}
    match event:
        case RunStarted():
            record |= {"event": "agent_run_started", "model": event.model}
            record |= {"prompt_version": PROMPT_VERSION} | asdict(event.limits)
        case ModelCalled():
            record |= {
                "event": "model_call",
                "turn": event.turn,
                "stop_reason": event.stop_reason,
                "model": event.model,
                "duration_ms": event.duration_ms,
            } | event.usage.model_dump()
        case ToolCalled():
            r = event.record
            record |= {
                "event": "tool_call",
                "turn": r.turn,
                "tool": r.name,
                "is_error": r.is_error,
                "result_chars": r.result_chars,
                "duration_ms": r.duration_ms,
            }
        case RunFinished():
            run = event.run
            record |= {
                "event": "agent_run_finished",
                "outcome": run.outcome,
                "turns": run.turns,
                "tool_calls": len(run.tool_calls),
                "duration_ms": run.duration_ms,
            } | run.usage.model_dump()
    print(json.dumps(record), file=sys.stderr, flush=True)


async def run_agent(
    question: str,
    *,
    model: ModelClient,
    toolbox: Toolbox,
    limits: Limits = DEFAULT_LIMITS,
    observe: Observer = json_log,
    clock: Callable[[], float] = time.monotonic,
) -> AgentRun:
    run_id = uuid.uuid4().hex[:12]
    started = clock()
    system = build_system_prompt(toolbox.instructions)
    tools = toolbox.specs()
    messages: list[Message] = [{"role": "user", "content": question}]
    usage = TokenUsage()
    calls: list[ToolCallRecord] = []
    served_by: list[str] = []
    observe(RunStarted(run_id, model.name, limits))

    def finish(outcome: Outcome, answer: str | None = None) -> AgentRun:
        run = AgentRun(
            run_id=run_id,
            question=question,
            answer=answer,
            outcome=outcome,
            model=model.name,
            served_by=served_by,
            prompt_version=PROMPT_VERSION,
            turns=len(served_by),
            tool_calls=calls,
            usage=usage,
            duration_ms=round((clock() - started) * 1000, 1),
            transcript=messages,
        )
        observe(RunFinished(run_id, run))
        return run

    def time_left() -> float:
        return limits.time_limit_s - (clock() - started)

    async def call(use: ToolUse, turn: int) -> Block:
        began = time.perf_counter()
        outcome = await toolbox.call(use.name, use.input)
        record = ToolCallRecord(
            turn=turn,
            name=use.name,
            arguments=use.input,
            is_error=outcome.is_error,
            result_chars=len(outcome.text),
            duration_ms=round((time.perf_counter() - began) * 1000, 1),
        )
        calls.append(record)
        observe(ToolCalled(run_id, record))
        return {
            "type": "tool_result",
            "tool_use_id": use.id,
            "content": outcome.text,
            "is_error": outcome.is_error,
        }

    while True:
        if time_left() <= 0:
            return finish("time_limit")
        if usage.total >= limits.token_budget:
            return finish("token_limit")

        turn = len(served_by) + 1
        request = ModelRequest(
            system=system, tools=tools, messages=messages, max_tokens=limits.max_tokens_per_reply
        )
        began = time.perf_counter()
        try:
            async with asyncio.timeout(time_left()):
                reply = await model.complete(request)
        except TimeoutError:
            return finish("time_limit")
        usage += reply.usage
        served_by.append(reply.model)
        messages.append({"role": "assistant", "content": reply.content})
        observe(
            ModelCalled(
                run_id,
                turn,
                reply.stop_reason,
                reply.model,
                reply.usage,
                round((time.perf_counter() - began) * 1000, 1),
            )
        )

        match reply.stop_reason:
            case "end_turn":
                answer = reply.text().strip()
                return finish("answered", answer) if answer else finish("no_answer")
            case "max_tokens":
                return finish("max_tokens")
            case "refusal":
                return finish("refused")
            case "tool_use":
                pass
            case _:
                return finish("unexpected_stop")

        try:
            uses = reply.tool_uses()
        except ValueError:
            return finish("unexpected_stop")
        if not uses:
            return finish("unexpected_stop")
        if len(calls) + len(uses) > limits.max_tool_calls:
            return finish("tool_limit")
        try:
            async with asyncio.timeout(time_left()):
                results = await asyncio.gather(*(call(use, turn) for use in uses))
        except TimeoutError:
            return finish("time_limit")
        messages.append({"role": "user", "content": list(results)})
