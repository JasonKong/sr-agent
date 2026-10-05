"""The runner: every case through the agent, concurrently, errors kept per case."""

import asyncio
from typing import Any

from sr_agent.loop import Limits
from sr_agent.model import ModelReply, ModelRequest, ModelUnavailable
from sr_agent.testing import FakeModel, FakeToolbox, answer, reply, tool_use
from sr_agent.toolbox import ToolOutcome
from sr_db.domain import FailureCode
from sr_evals.dataset import EvalCase
from sr_evals.runner import CaseResult, run_cases


def case(n: int) -> EvalCase:
    return EvalCase(
        case_id=f"F07-{n}",
        question=f"What happened to GR-{n}?",
        reference="composer_id",
        composer_id=f"GR-{n}",
        hub_id=None,
        code=FailureCode.F07_CLINIC_PMS_AUTH_FAILED,
        distractor=None,
        expected_evidence=[],
        escalate_to_engineering=False,
    )


class Slow(FakeModel):
    running = 0
    peak = 0

    async def complete(self, request: ModelRequest) -> ModelReply:
        Slow.running += 1
        Slow.peak = max(Slow.peak, Slow.running)
        await asyncio.sleep(0.02)
        Slow.running -= 1
        return await super().complete(request)


def test_every_case_runs_in_dataset_order_within_the_concurrency_limit() -> None:
    cases = [case(n) for n in range(1, 7)]
    tools = FakeToolbox({"get_referral": lambda args: ToolOutcome(text='{"id":"GR-1"}')})
    seen: list[str] = []

    def model_for(c: EvalCase) -> Slow:
        return Slow(reply(tool_use("get_referral", referral_id=c.composer_id)), answer())

    results = asyncio.run(
        run_cases(
            cases,
            model_for=model_for,
            toolbox=tools,
            concurrency=2,
            on_case=lambda r: seen.append(r.case.case_id),
        )
    )

    assert [r.case.case_id for r in results] == [c.case_id for c in cases]
    assert all(r.run is not None and r.run.outcome == "answered" for r in results)
    assert Slow.peak == 2
    assert sorted(seen) == sorted(c.case_id for c in cases)
    assert sorted(args["referral_id"] for _, args in tools.calls) == [c.composer_id for c in cases]


def test_a_service_failure_in_one_case_does_not_stop_the_others() -> None:
    class Down(FakeModel):
        async def complete(self, request: ModelRequest) -> ModelReply:
            raise ModelUnavailable("the model API failed (529): overloaded")

    def model_for(c: EvalCase) -> Any:
        return Down() if c.case_id == "F07-2" else FakeModel(answer())

    results: list[CaseResult] = asyncio.run(
        run_cases(
            [case(1), case(2), case(3)],
            model_for=model_for,
            toolbox=FakeToolbox({}),
            limits=Limits(max_tool_calls=2),
        )
    )

    assert [r.error for r in results] == [
        None,
        "ModelUnavailable: the model API failed (529): overloaded",
        None,
    ]
    assert results[1].run is None
    assert results[0].run is not None
