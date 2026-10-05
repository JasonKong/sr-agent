"""The diagnosis loop: a hand-written tool-use loop (DESIGN.md section 7.4).

Each turn sends the whole conversation to the model. If it asks for tools, the loop calls
them through the toolbox, appends the results as one user message, and goes round again.
It ends when the model gives an answer that passes the checks, or when a limit is reached.

- **The history is append-only.** Every reply is appended exactly as received, thinking
  blocks included, and nothing earlier is ever edited. The model API rejects edited
  histories that carry thinking blocks, and an unchanged prefix is what prompt caching
  reuses.
- **The answer is checked in code.** It must fit the schema, and each evidence id must
  have appeared in a tool result in this run. A failing answer gets one repair turn that
  says what is wrong. Evidence that still does not resolve is dropped and recorded.
- **Limits belong to the loop**, because they are a policy for one diagnosis whatever the
  model provider or transport: tool calls, wall-clock time and tokens. Every model and tool
  await runs under `asyncio.timeout` with the time left, so retries inside the SDK cannot
  overrun it. On reaching a limit, one wrap-up turn without tools asks for the best answer
  from the evidence so far; the run is marked incomplete either way.
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

from sr_agent.diagnosis import AnswerCheck, Diagnosis, check_answer, ids_in, model_output_schema
from sr_agent.model import (
    Block,
    Message,
    ModelClient,
    ModelReply,
    ModelRequest,
    TokenUsage,
    ToolUse,
)
from sr_agent.prompts import PROMPT_VERSION, build_system_prompt
from sr_agent.safety import clinical_advice, mask_identifiers, scrub_question
from sr_agent.toolbox import Toolbox

Limit = Literal["tool_limit", "time_limit", "token_limit"]
Outcome = Literal[
    "answered",
    "tool_limit",
    "time_limit",
    "token_limit",
    "max_tokens",  # the reply hit its own output limit
    "refused",
    "invalid_output",  # no answer fitting the schema, even after the repair turn
    "unexpected_stop",  # a stop reason or reply the loop cannot act on
]

_LIMIT_NAMES: dict[Limit, str] = {
    "tool_limit": "the tool-call limit",
    "time_limit": "the time limit",
    "token_limit": "the token budget",
}


@dataclass(frozen=True)
class Limits:
    max_tool_calls: int = 8
    time_limit_s: float = 60.0
    token_budget: int = 200_000  # every token processed in the run, cached or not
    max_tokens_per_reply: int = 16_000
    wrap_up_s: float = 20.0  # extra time for the closing answer after a limit
    max_repairs: int = 1


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
    diagnosis: Diagnosis | None
    outcome: Outcome
    model: str  # requested
    served_by: list[str]  # the model that served each turn
    prompt_version: str
    turns: int
    repairs: int  # answers sent back for correction
    dropped_evidence: list[str]  # cited ids that never resolved, removed from the diagnosis
    question_redactions: int  # patient identifiers masked in the question before the model
    output_redactions: int  # patient identifiers masked in the answer
    safety_flags: list[str]  # clinical advice still present after the repair turn
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
                "repairs": run.repairs,
                "dropped_evidence": len(run.dropped_evidence),
                "duration_ms": run.duration_ms,
            } | run.usage.model_dump()
    print(json.dumps(record), file=sys.stderr, flush=True)


def _repair(problems: list[str]) -> Message:
    text = (
        "Your answer could not be accepted: "
        + "; ".join(problems)
        + ". Reply again with the corrected answer. Cite only record ids that appear in "
        "the tool results; call a tool first if you need to check a record."
    )
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _closing(limit: Limit, last: Message) -> Message:
    """The wrap-up prompt, answering any tool calls the limit stopped from running."""
    name = _LIMIT_NAMES[limit]
    blocks: list[Block] = []
    if last["role"] == "assistant":
        blocks += [
            {
                "type": "tool_result",
                "tool_use_id": b["id"],
                "content": f"Not run: {name} for this diagnosis was reached.",
                "is_error": True,
            }
            for b in last["content"]
            if b.get("type") == "tool_use" and "id" in b
        ]
    blocks.append(
        {
            "type": "text",
            "text": f"You have reached {name} for this diagnosis. Do not call any more tools. "
            "Answer now from the evidence you already have, and lower your confidence if it "
            "does not settle the question.",
        }
    )
    return {"role": "user", "content": blocks}


async def run_agent(
    question: str,
    *,
    model: ModelClient,
    toolbox: Toolbox,
    limits: Limits = DEFAULT_LIMITS,
    observe: Observer = json_log,
    clock: Callable[[], float] = time.monotonic,
    prompt_version: str = PROMPT_VERSION,
) -> AgentRun:
    run_id = uuid.uuid4().hex[:12]
    started = clock()
    tools = toolbox.specs()
    system = build_system_prompt(
        toolbox.instructions, {t["name"] for t in tools}, version=prompt_version
    )
    schema = model_output_schema()
    question, question_redactions = scrub_question(question)
    messages: list[Message] = [{"role": "user", "content": question}]
    usage = TokenUsage()
    calls: list[ToolCallRecord] = []
    served_by: list[str] = []
    seen: set[str] = set()  # evidence ids that appeared in successful tool results
    dropped: list[str] = []
    flags: list[str] = []
    repairs = 0
    output_redactions = 0
    observe(RunStarted(run_id, model.name, limits))

    def finish(outcome: Outcome, diagnosis: Diagnosis | None = None) -> AgentRun:
        nonlocal output_redactions
        if diagnosis is not None:
            diagnosis, output_redactions = mask_identifiers(diagnosis)
        run = AgentRun(
            run_id=run_id,
            question=question,
            diagnosis=diagnosis,
            outcome=outcome,
            model=model.name,
            served_by=served_by,
            prompt_version=prompt_version,
            turns=len(served_by),
            repairs=repairs,
            dropped_evidence=dropped,
            question_redactions=question_redactions,
            output_redactions=output_redactions,
            safety_flags=flags,
            tool_calls=calls,
            usage=usage,
            duration_ms=round((clock() - started) * 1000, 1),
            transcript=messages,
        )
        observe(RunFinished(run_id, run))
        return run

    def time_left() -> float:
        return limits.time_limit_s - (clock() - started)

    async def ask(tool_choice: Literal["auto", "none"], timeout: float) -> ModelReply | None:
        """One model turn, appended to the history; None if it ran out of time."""
        nonlocal usage
        request = ModelRequest(
            system=system,
            tools=tools,
            messages=messages,
            max_tokens=limits.max_tokens_per_reply,
            output_schema=schema,
            tool_choice=tool_choice,
        )
        began = time.perf_counter()
        try:
            async with asyncio.timeout(timeout):
                reply = await model.complete(request)
        except TimeoutError:
            return None
        usage += reply.usage
        served_by.append(reply.model)
        messages.append({"role": "assistant", "content": reply.content})
        duration = round((time.perf_counter() - began) * 1000, 1)
        observe(
            ModelCalled(
                run_id, len(served_by), reply.stop_reason, reply.model, reply.usage, duration
            )
        )
        return reply

    async def call(use: ToolUse, turn: int) -> Block:
        began = time.perf_counter()
        outcome = await toolbox.call(use.name, use.input)
        if not outcome.is_error:
            seen.update(ids_in(outcome.text))
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

    def settle(outcome: Outcome, check: AnswerCheck, advice: list[str]) -> AgentRun:
        """Accept an answer as it is: drop unresolved evidence and an advising draft."""
        if check.diagnosis is None:
            return finish("invalid_output" if outcome == "answered" else outcome)
        dropped.extend(check.unresolved)
        flags.extend(advice)
        diagnosis = check.diagnosis
        if any(f.startswith("draft_customer_reply:") for f in advice):
            diagnosis = diagnosis.model_copy(update={"draft_customer_reply": None})
        return finish(outcome, diagnosis)

    async def wrap_up(limit: Limit) -> AgentRun:
        messages.append(_closing(limit, messages[-1]))
        reply = await ask("none", limits.wrap_up_s)
        if reply is None or reply.stop_reason != "end_turn":
            return finish(limit)
        check = check_answer(reply.text(), seen=seen)
        advice = clinical_advice(check.diagnosis) if check.diagnosis else []
        return settle(limit, check, advice)

    while True:
        if time_left() <= 0:
            return await wrap_up("time_limit")
        if usage.total >= limits.token_budget:
            return await wrap_up("token_limit")

        reply = await ask("auto", time_left())
        if reply is None:
            return await wrap_up("time_limit")

        match reply.stop_reason:
            case "end_turn":
                check = check_answer(reply.text(), seen=seen)
                advice = clinical_advice(check.diagnosis) if check.diagnosis else []
                problems = list(check.problems)
                if advice:
                    problems.append(
                        "the answer gives clinical advice, which is out of scope and must be "
                        "removed (" + "; ".join(advice) + ")"
                    )
                if not problems:
                    return finish("answered", check.diagnosis)
                if repairs < limits.max_repairs:
                    repairs += 1
                    messages.append(_repair(problems))
                    continue
                return settle("answered", check, advice)
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
            return await wrap_up("tool_limit")
        try:
            async with asyncio.timeout(time_left()):
                results = await asyncio.gather(*(call(use, len(served_by)) for use in uses))
        except TimeoutError:
            return await wrap_up("time_limit")
        messages.append({"role": "user", "content": list(results)})
