"""The tool-use loop, driven by a scripted FakeModel and a FakeToolbox (no network)."""

import asyncio
import itertools
import json
import time
from typing import Any

import pytest

from sr_agent.loop import AgentEvent, Limits, ToolCalled, json_log, run_agent
from sr_agent.model import ModelReply, ModelRequest, TokenUsage
from sr_agent.testing import (
    FakeClock,
    FakeModel,
    FakeToolbox,
    reply,
    text,
    thinking,
    tool_use,
)
from sr_agent.toolbox import ToolOutcome

QUESTION = "Why didn't the specialist get GR-10234?"


def toolbox(**handlers: Any) -> FakeToolbox:
    return FakeToolbox(handlers or {"get_referral": lambda args: ToolOutcome(text='{"ok":1}')})


def run(model: FakeModel, tools: FakeToolbox, **kwargs: Any) -> Any:
    return asyncio.run(run_agent(QUESTION, model=model, toolbox=tools, **kwargs))


def test_a_direct_answer_needs_one_model_call() -> None:
    model = FakeModel(reply(text("Nothing is wrong.")))
    result = run(model, toolbox())

    assert result.outcome == "answered"
    assert result.answer == "Nothing is wrong."
    assert not result.incomplete
    assert result.turns == 1
    assert result.tool_calls == []
    assert model.requests[0].messages == [{"role": "user", "content": QUESTION}]


def test_a_tool_call_goes_to_the_toolbox_and_its_result_back_to_the_model() -> None:
    tools = FakeToolbox(
        {"get_referral": lambda args: ToolOutcome(text=f"seen {args['referral_id']}")}
    )
    model = FakeModel(
        reply(tool_use("get_referral", id="toolu_a", referral_id="GR-10234")),
        reply(text("It was routed MANUAL.")),
    )
    result = run(model, tools)

    assert tools.calls == [("get_referral", {"referral_id": "GR-10234"})]
    last = model.requests[1].messages[-1]
    assert last == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_a",
                "content": "seen GR-10234",
                "is_error": False,
            }
        ],
    }
    assert result.answer == "It was routed MANUAL."
    assert [(c.name, c.arguments, c.is_error) for c in result.tool_calls] == [
        ("get_referral", {"referral_id": "GR-10234"}, False)
    ]
    assert result.tool_calls[0].result_chars == len("seen GR-10234")


def test_parallel_tool_calls_go_back_in_one_message_in_the_order_asked() -> None:
    tools = FakeToolbox(
        {
            "slow": lambda args: ToolOutcome(text="slow result"),
            "fast": lambda args: ToolOutcome(text="fast result"),
        },
        delays={"slow": 0.05},
    )
    model = FakeModel(
        reply(tool_use("slow", id="t1"), tool_use("fast", id="t2")),
        reply(text("done")),
    )
    run(model, tools)

    results = model.requests[1].messages[-1]["content"]
    assert [(r["tool_use_id"], r["content"]) for r in results] == [
        ("t1", "slow result"),
        ("t2", "fast result"),
    ]
    assert len(model.requests[1].messages) == 3  # question, assistant, one user message


def test_tool_errors_go_back_to_the_model_as_errors() -> None:
    tools = FakeToolbox(
        {"get_referral": lambda args: ToolOutcome(text="NOT_FOUND: GR-1", is_error=True)}
    )
    model = FakeModel(
        reply(tool_use("get_referral", id="t1", referral_id="GR-1")),
        reply(text("That referral does not exist.")),
    )
    result = run(model, tools)

    block = model.requests[1].messages[-1]["content"][0]
    assert block["is_error"] is True
    assert block["content"] == "NOT_FOUND: GR-1"
    assert result.outcome == "answered"
    assert result.tool_calls[0].is_error


def test_history_is_append_only_and_replays_the_model_output_verbatim() -> None:
    first = reply(thinking("", signature="sig-1"), tool_use("get_referral", id="t1"))
    second = reply(
        thinking("checking the deliveries", signature="sig-2"),
        text("Looking at deliveries next."),
        tool_use("get_referral", id="t2"),
    )
    model = FakeModel(first, second, reply(text("done")))
    run(model, toolbox())

    requests = model.requests
    for before, after in itertools.pairwise(requests):
        assert after.messages[: len(before.messages)] == before.messages
    assert requests[1].messages[1] == {"role": "assistant", "content": first.content}
    assert requests[2].messages[3] == {"role": "assistant", "content": second.content}


def test_the_system_prompt_and_tools_are_identical_on_every_request() -> None:
    model = FakeModel(
        reply(tool_use("get_referral", id="t1")),
        reply(tool_use("get_referral", id="t2")),
        reply(text("done")),
    )
    run(model, toolbox())

    assert len({r.system for r in model.requests}) == 1
    assert all(r.tools == model.requests[0].tools for r in model.requests)
    assert model.requests[0].tools == toolbox().specs()
    assert "not enough evidence" in model.requests[0].system


def endless_tool_calls(request: ModelRequest) -> ModelReply:
    return reply(tool_use("get_referral", referral_id="GR-1"))


def test_the_tool_budget_is_never_exceeded() -> None:
    tools = toolbox()
    model = FakeModel(*[endless_tool_calls] * 10)
    result = run(model, tools, limits=Limits(max_tool_calls=3))

    assert len(tools.calls) == 3
    assert result.outcome == "tool_limit"
    assert result.incomplete
    assert result.answer is None
    assert len(model.requests) == 4  # the 4th reply asked for a call over the budget


def test_a_batch_that_would_exceed_the_budget_is_not_run_at_all() -> None:
    tools = toolbox()
    model = FakeModel(
        reply(tool_use("get_referral", id="t1")),
        reply(tool_use("get_referral", id="t2"), tool_use("get_referral", id="t3")),
    )
    result = run(model, tools, limits=Limits(max_tool_calls=2))

    assert len(tools.calls) == 1
    assert result.outcome == "tool_limit"


def test_the_time_limit_stops_the_run_before_the_next_model_call() -> None:
    clock = FakeClock()
    model = FakeModel(*[endless_tool_calls] * 10, clock=clock, latency_s=25)
    result = run(model, toolbox(), limits=Limits(time_limit_s=60), clock=clock)

    assert len(model.requests) == 3  # at 0 s, 25 s and 50 s; the next would start at 75 s
    assert result.outcome == "time_limit"


def test_a_slow_model_call_is_cut_off_at_the_time_limit() -> None:
    model = FakeModel(reply(text("too late")), sleep_s=5)
    started = time.perf_counter()
    result = run(model, toolbox(), limits=Limits(time_limit_s=0.2))

    assert result.outcome == "time_limit"
    assert time.perf_counter() - started < 2


def test_a_slow_tool_is_cut_off_at_the_time_limit() -> None:
    tools = FakeToolbox({"get_referral": lambda args: ToolOutcome(text="x")}, delays={"*": 5})
    model = FakeModel(reply(tool_use("get_referral")), reply(text("too late")))
    started = time.perf_counter()
    result = run(model, tools, limits=Limits(time_limit_s=0.2))

    assert result.outcome == "time_limit"
    assert time.perf_counter() - started < 2


def test_the_token_budget_stops_the_run_before_the_next_model_call() -> None:
    usage = TokenUsage(input_tokens=100, cache_read_input_tokens=400, output_tokens=100)
    model = FakeModel(*[reply(tool_use("get_referral"), usage=usage) for _ in range(5)])
    result = run(model, toolbox(), limits=Limits(token_budget=1000))

    assert len(model.requests) == 2  # 600 tokens, then 1,200: over the budget
    assert result.outcome == "token_limit"
    assert result.usage.total == 1200


@pytest.mark.parametrize(
    ("last", "outcome"),
    [
        (reply(text("half an ans"), stop_reason="max_tokens"), "max_tokens"),
        (reply(stop_reason="refusal"), "refused"),
        (reply(text("paused"), stop_reason="pause_turn"), "unexpected_stop"),
        (reply(thinking(""), stop_reason="end_turn"), "no_answer"),
        (reply(text("no tool after all"), stop_reason="tool_use"), "unexpected_stop"),
        (reply({"type": "tool_use", "name": "get_referral"}), "unexpected_stop"),  # no id
    ],
)
def test_other_stop_reasons_end_the_run_incomplete(last: ModelReply, outcome: str) -> None:
    result = run(FakeModel(last), toolbox())

    assert result.outcome == outcome
    assert result.incomplete
    assert result.answer is None


def test_usage_and_the_models_that_served_each_turn_are_recorded() -> None:
    model = FakeModel(
        reply(tool_use("get_referral"), usage=TokenUsage(input_tokens=10, output_tokens=5)),
        reply(
            text("done"),
            usage=TokenUsage(cache_read_input_tokens=7, cache_creation_input_tokens=3),
            model="fallback-model",
        ),
    )
    result = run(model, toolbox())

    assert result.usage == TokenUsage(
        input_tokens=10, output_tokens=5, cache_read_input_tokens=7, cache_creation_input_tokens=3
    )
    assert result.model == model.name
    assert result.served_by == [model.name, "fallback-model"]
    assert result.transcript[-1] == {"role": "assistant", "content": [text("done")]}
    assert result.duration_ms >= 0


def test_observers_see_each_model_and_tool_call() -> None:
    events: list[AgentEvent] = []
    model = FakeModel(reply(tool_use("get_referral", referral_id="GR-1")), reply(text("done")))
    result = run(model, toolbox(), observe=events.append)

    names = [type(e).__name__ for e in events]
    assert names == ["RunStarted", "ModelCalled", "ToolCalled", "ModelCalled", "RunFinished"]
    assert {e.run_id for e in events} == {result.run_id}
    tool_event = events[2]
    assert isinstance(tool_event, ToolCalled)
    assert tool_event.record.arguments == {"referral_id": "GR-1"}


def test_the_json_log_carries_the_correlation_id_but_no_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_result = "ticket text that must stay out of logs"
    tools = FakeToolbox({"get_referral": lambda args: ToolOutcome(text=secret_result)})
    model = FakeModel(
        reply(tool_use("get_referral", referral_id="GR-777")), reply(text("the final answer"))
    )
    result = run(model, tools, observe=json_log)

    err = capsys.readouterr().err
    lines = [json.loads(line) for line in err.splitlines()]
    assert [line["event"] for line in lines] == [
        "agent_run_started",
        "model_call",
        "tool_call",
        "model_call",
        "agent_run_finished",
    ]
    assert all(line["correlation_id"] == result.run_id for line in lines)
    assert lines[2]["tool"] == "get_referral"
    assert lines[-1]["outcome"] == "answered"
    for content in (QUESTION, secret_result, "GR-777", "the final answer"):
        assert content not in err
