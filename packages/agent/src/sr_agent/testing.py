"""Fakes for testing the loop without a model API or an MCP server.

`FakeModel` plays a script: each step is a reply, or a function of the request that returns
one. It records a deep copy of every request, so a test can check what the model was sent
on each turn, and it fails loudly if the loop asks for more turns than the script has.
"""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
from collections.abc import Callable, Mapping
from typing import Any

from sr_agent.model import Block, ModelReply, ModelRequest, TokenUsage
from sr_agent.toolbox import ToolOutcome

_ids = itertools.count(1)


def text(value: str) -> Block:
    return {"type": "text", "text": value}


def thinking(value: str = "", signature: str = "fake-signature") -> Block:
    return {"type": "thinking", "thinking": value, "signature": signature}


def tool_use(name: str, id: str | None = None, **arguments: Any) -> Block:
    return {
        "type": "tool_use",
        "id": id or f"toolu_fake_{next(_ids)}",
        "name": name,
        "input": arguments,
    }


def reply(
    *blocks: Block,
    stop_reason: str | None = None,
    usage: TokenUsage | None = None,
    model: str = "fake-model",
) -> ModelReply:
    """A model reply. The stop reason follows from the blocks unless given."""
    if stop_reason is None:
        stop_reason = "tool_use" if any(b.get("type") == "tool_use" for b in blocks) else "end_turn"
    return ModelReply(
        content=list(blocks), stop_reason=stop_reason, usage=usage or TokenUsage(), model=model
    )


def diagnosis_json(
    *,
    summary: str = "The clinic's PMS token expired, so the push into its system failed.",
    root_cause_code: str | None = "F07_CLINIC_PMS_AUTH_FAILED",
    confidence: str = "high",
    evidence: list[tuple[str, str]] | None = None,
    recommended_action: str = "Ask the clinic to re-authorise its PMS connection, then resend.",
    draft_customer_reply: str | None = None,
    escalate_to_engineering: bool = False,
) -> str:
    """An answer in the model's output schema; evidence is (record_id, detail) pairs."""
    return json.dumps(
        {
            "summary": summary,
            "root_cause_code": root_cause_code,
            "confidence": confidence,
            "evidence": [{"record_id": rid, "detail": detail} for rid, detail in evidence or []],
            "recommended_action": recommended_action,
            "draft_customer_reply": draft_customer_reply,
            "escalate_to_engineering": escalate_to_engineering,
        }
    )


def answer(**fields: Any) -> ModelReply:
    """A final reply carrying a diagnosis (see `diagnosis_json` for the fields)."""
    return reply(text(diagnosis_json(**fields)))


Step = ModelReply | Callable[[ModelRequest], ModelReply]


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeModel:
    name = "fake-model"

    def __init__(
        self,
        *steps: Step,
        clock: FakeClock | None = None,
        latency_s: float = 0.0,
        sleep_s: float = 0.0,
    ) -> None:
        """`latency_s` advances the fake clock per call; `sleep_s` really waits."""
        self._steps = list(steps)
        self._clock = clock
        self._latency_s = latency_s
        self._sleep_s = sleep_s
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(copy.deepcopy(request))
        if not self._steps:
            raise AssertionError(f"FakeModel script exhausted after {len(self.requests) - 1} calls")
        step = self._steps.pop(0)
        if self._clock is not None:
            self._clock.advance(self._latency_s)
        if self._sleep_s:
            await asyncio.sleep(self._sleep_s)
        result = step(request) if callable(step) else step
        return result.model_copy(deep=True)


class FakeToolbox:
    """Tools as plain functions. `delays` maps a tool name, or "*", to seconds to wait."""

    def __init__(
        self,
        handlers: Mapping[str, Callable[[dict[str, Any]], ToolOutcome]],
        *,
        delays: Mapping[str, float] | None = None,
        instructions: str = "Fake platform instructions.",
    ) -> None:
        self._handlers = dict(handlers)
        self._delays = dict(delays or {})
        self._instructions = instructions
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def instructions(self) -> str:
        return self._instructions

    def specs(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "description": f"Fake {name}.", "input_schema": {"type": "object"}}
            for name in self._handlers
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        self.calls.append((name, arguments))
        delay = self._delays.get(name, self._delays.get("*", 0.0))
        if delay:
            await asyncio.sleep(delay)
        handler = self._handlers.get(name)
        if handler is None:
            return ToolOutcome(text=f"Unknown tool: {name}", is_error=True)
        return handler(arguments)
