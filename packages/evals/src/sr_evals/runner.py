"""Run the agent over eval cases, concurrently, keeping a service failure to its own case."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sr_agent.loop import DEFAULT_LIMITS, AgentEvent, AgentRun, Limits, run_agent
from sr_agent.model import ModelClient, ModelUnavailable
from sr_agent.toolbox import Toolbox, ToolsUnavailable
from sr_evals.dataset import EvalCase


@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    run: AgentRun | None
    error: str | None  # set when the model API or the MCP server failed for this case


def _quiet(event: AgentEvent) -> None:
    return None


async def run_cases(
    cases: Sequence[EvalCase],
    *,
    model_for: Callable[[EvalCase], ModelClient],
    toolbox: Toolbox,
    limits: Limits = DEFAULT_LIMITS,
    concurrency: int = 4,
    on_case: Callable[[CaseResult], None] | None = None,
) -> list[CaseResult]:
    """Results in the order of `cases`. `on_case` sees each result as it finishes."""
    gate = asyncio.Semaphore(concurrency)

    async def one(case: EvalCase) -> CaseResult:
        async with gate:
            try:
                run = await run_agent(
                    case.question,
                    model=model_for(case),
                    toolbox=toolbox,
                    limits=limits,
                    observe=_quiet,
                )
                result = CaseResult(case, run, None)
            except (ModelUnavailable, ToolsUnavailable) as error:
                result = CaseResult(case, None, f"{type(error).__name__}: {error}")
        if on_case is not None:
            on_case(result)
        return result

    return list(await asyncio.gather(*(one(c) for c in cases)))
