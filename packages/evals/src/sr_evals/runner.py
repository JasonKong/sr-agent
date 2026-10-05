"""Run the agent over eval cases, concurrently, keeping a service failure to its own case.

With a cost cap, cases stop starting once the finished ones have cost that much. Cases
already running finish, so a run can pass the cap by up to `concurrency - 1` cases. A case
that never started is an error, and can be run later with `--cases`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sr_agent.loop import DEFAULT_LIMITS, AgentEvent, AgentRun, Limits, run_agent
from sr_agent.model import ModelClient, ModelUnavailable
from sr_agent.prompts import PROMPT_VERSION
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
    prompt_version: str = PROMPT_VERSION,
    max_cost_usd: float | None = None,
) -> list[CaseResult]:
    """Results in the order of `cases`. `on_case` sees each result as it finishes."""
    gate = asyncio.Semaphore(concurrency)
    spent = 0.0

    async def one(case: EvalCase) -> CaseResult:
        nonlocal spent
        async with gate:
            if max_cost_usd is not None and spent >= max_cost_usd:
                capped = f"not run: the cost cap of ${max_cost_usd:.2f} was reached"
                result = CaseResult(case, None, capped)
            else:
                result = await attempt(case)
                if result.run is not None:
                    spent += result.run.cost_usd or 0.0  # an unpriced model cannot be capped
        if on_case is not None:
            on_case(result)
        return result

    async def attempt(case: EvalCase) -> CaseResult:
        try:
            run = await run_agent(
                case.question,
                model=model_for(case),
                toolbox=toolbox,
                limits=limits,
                observe=_quiet,
                prompt_version=prompt_version,
            )
        except (ModelUnavailable, ToolsUnavailable) as error:
            return CaseResult(case, None, f"{type(error).__name__}: {error}")
        return CaseResult(case, run, None)

    return list(await asyncio.gather(*(one(c) for c in cases)))
