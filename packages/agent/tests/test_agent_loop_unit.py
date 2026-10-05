"""The tool-use loop, driven by a scripted FakeModel and a FakeToolbox (no network)."""

import asyncio
import itertools
import json
import time
from typing import Any

import pytest

from sr_agent.diagnosis import model_output_schema
from sr_agent.loop import AgentEvent, Limits, ToolCalled, json_log, run_agent
from sr_agent.model import ModelReply, ModelRequest, TokenUsage
from sr_agent.testing import (
    FakeClock,
    FakeModel,
    FakeToolbox,
    answer,
    diagnosis_json,
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
    model = FakeModel(answer(summary="Nothing is wrong.", root_cause_code="F00_NO_FAULT"))
    result = run(model, toolbox())

    assert result.outcome == "answered"
    assert result.diagnosis is not None
    assert result.diagnosis.summary == "Nothing is wrong."
    assert result.diagnosis.root_cause_code == "F00_NO_FAULT"
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
        answer(summary="It was routed MANUAL."),
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
    assert result.diagnosis is not None
    assert result.diagnosis.summary == "It was routed MANUAL."
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
        answer(),
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
        answer(root_cause_code=None),
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
    model = FakeModel(first, second, answer())
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
        answer(),
    )
    run(model, toolbox())

    assert len({r.system for r in model.requests}) == 1
    assert all(r.tools == model.requests[0].tools for r in model.requests)
    assert model.requests[0].tools == toolbox().specs()
    assert "not enough evidence" in model.requests[0].system


def test_every_request_asks_for_the_answer_schema_and_leaves_tool_choice_to_the_model() -> None:
    model = FakeModel(reply(tool_use("get_referral")), answer())
    run(model, toolbox())

    assert all(r.output_schema == model_output_schema() for r in model.requests)
    assert all(r.tool_choice == "auto" for r in model.requests)


def cited(record_ids: list[str]) -> ModelReply:
    return answer(evidence=[(rid, "what it shows") for rid in record_ids])


def tools_that_show(*record_ids: str) -> FakeToolbox:
    body = json.dumps({"ids": list(record_ids)}, separators=(",", ":"))
    return FakeToolbox({"get_referral": lambda args: ToolOutcome(text=body)})


def test_an_answer_citing_records_never_seen_gets_one_repair_turn() -> None:
    model = FakeModel(
        reply(tool_use("get_referral")), cited(["dl_1", "dl_999"]), cited(["dl_1", "GR-5"])
    )
    result = run(model, tools_that_show("dl_1", "GR-5"))

    repair = model.requests[2].messages[-1]
    assert repair["role"] == "user"
    assert "dl_999" in repair["content"][0]["text"]
    assert result.outcome == "answered"
    assert result.repairs == 1
    assert result.dropped_evidence == []
    assert result.diagnosis is not None
    assert [e.record_id for e in result.diagnosis.evidence] == ["dl_1", "GR-5"]


def test_evidence_still_unresolved_after_the_repair_is_dropped_and_recorded() -> None:
    model = FakeModel(reply(tool_use("get_referral")), cited(["dl_999"]), cited(["dl_1", "dl_999"]))
    result = run(model, tools_that_show("dl_1"))

    assert result.outcome == "answered"
    assert result.diagnosis is not None
    assert [e.record_id for e in result.diagnosis.evidence] == ["dl_1"]
    assert result.dropped_evidence == ["dl_999"]


def test_an_answer_that_never_fits_the_schema_is_invalid_output() -> None:
    model = FakeModel(reply(text("The token expired.")), reply(text("Still prose.")))
    result = run(model, toolbox())

    assert result.outcome == "invalid_output"
    assert result.diagnosis is None
    assert result.incomplete
    assert result.repairs == 1


def test_hitting_the_tool_limit_ends_with_a_partial_answer_from_the_evidence_so_far() -> None:
    model = FakeModel(
        reply(tool_use("get_referral", id="t1")),
        reply(tool_use("get_referral", id="t2")),
        answer(confidence="low"),
    )
    result = run(model, toolbox(), limits=Limits(max_tool_calls=1))

    wrap_up = model.requests[-1]
    assert wrap_up.tool_choice == "none"
    closing = wrap_up.messages[-1]["content"]
    assert closing[0]["type"] == "tool_result"
    assert closing[0]["tool_use_id"] == "t2"
    assert closing[0]["is_error"] is True
    assert "tool-call limit" in closing[-1]["text"]
    assert result.outcome == "tool_limit"
    assert result.incomplete
    assert result.diagnosis is not None
    assert result.diagnosis.confidence == "low"


def endless_tool_calls(request: ModelRequest) -> ModelReply:
    return reply(tool_use("get_referral", referral_id="GR-1"))


def test_the_tool_budget_is_never_exceeded() -> None:
    tools = toolbox()
    model = FakeModel(*[endless_tool_calls] * 10)
    result = run(model, tools, limits=Limits(max_tool_calls=3))

    assert len(tools.calls) == 3
    assert result.outcome == "tool_limit"
    assert result.incomplete
    assert result.diagnosis is None  # the wrap-up reply asked for a tool again
    assert len(model.requests) == 5  # the 4th reply went over the budget; the 5th wraps up


def test_a_batch_that_would_exceed_the_budget_is_not_run_at_all() -> None:
    tools = toolbox()
    model = FakeModel(
        reply(tool_use("get_referral", id="t1")),
        reply(tool_use("get_referral", id="t2"), tool_use("get_referral", id="t3")),
        answer(),
    )
    result = run(model, tools, limits=Limits(max_tool_calls=2))

    assert len(tools.calls) == 1
    assert result.outcome == "tool_limit"


def test_the_time_limit_stops_the_run_before_the_next_model_call() -> None:
    clock = FakeClock()
    model = FakeModel(*[endless_tool_calls] * 10, clock=clock, latency_s=25)
    result = run(model, toolbox(), limits=Limits(time_limit_s=60), clock=clock)

    assert len(model.requests) == 4  # at 0 s, 25 s and 50 s, then the wrap-up at 75 s
    assert model.requests[-1].tool_choice == "none"
    assert result.outcome == "time_limit"


def test_a_slow_model_call_is_cut_off_at_the_time_limit() -> None:
    model = FakeModel(answer(), answer(), sleep_s=5)
    started = time.perf_counter()
    result = run(model, toolbox(), limits=Limits(time_limit_s=0.2, wrap_up_s=0.2))

    assert result.outcome == "time_limit"
    assert result.diagnosis is None  # the wrap-up was too slow as well
    assert time.perf_counter() - started < 2


def test_a_slow_tool_is_cut_off_at_the_time_limit() -> None:
    tools = FakeToolbox({"get_referral": lambda args: ToolOutcome(text="x")}, delays={"*": 5})
    model = FakeModel(reply(tool_use("get_referral", id="t1")), answer(confidence="low"))
    started = time.perf_counter()
    result = run(model, tools, limits=Limits(time_limit_s=0.2))

    assert result.outcome == "time_limit"
    assert result.diagnosis is not None  # answered from the evidence it had: none
    assert model.requests[-1].messages[-1]["content"][0]["tool_use_id"] == "t1"
    assert time.perf_counter() - started < 2


def test_the_token_budget_stops_the_run_before_the_next_model_call() -> None:
    usage = TokenUsage(input_tokens=100, cache_read_input_tokens=400, output_tokens=100)
    model = FakeModel(*[reply(tool_use("get_referral"), usage=usage) for _ in range(5)])
    result = run(model, toolbox(), limits=Limits(token_budget=1000))

    assert len(model.requests) == 3  # 600 tokens, then 1,200: over the budget; then wrap-up
    assert result.outcome == "token_limit"
    assert result.usage.total == 1800


@pytest.mark.parametrize(
    ("last", "outcome"),
    [
        (reply(text("half an ans"), stop_reason="max_tokens"), "max_tokens"),
        (reply(stop_reason="refusal"), "refused"),
        (reply(text("paused"), stop_reason="pause_turn"), "unexpected_stop"),
        (reply(thinking(""), stop_reason="end_turn"), "invalid_output"),
        (reply(text("no tool after all"), stop_reason="tool_use"), "unexpected_stop"),
        (reply({"type": "tool_use", "name": "get_referral"}), "unexpected_stop"),  # no id
    ],
)
def test_other_stop_reasons_end_the_run_incomplete(last: ModelReply, outcome: str) -> None:
    result = run(FakeModel(last, last), toolbox())  # twice: an invalid answer gets a repair

    assert result.outcome == outcome
    assert result.incomplete
    assert result.diagnosis is None


def test_usage_and_the_models_that_served_each_turn_are_recorded() -> None:
    model = FakeModel(
        reply(tool_use("get_referral"), usage=TokenUsage(input_tokens=10, output_tokens=5)),
        reply(
            text(diagnosis_json()),
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
    assert result.transcript[-1] == {"role": "assistant", "content": [text(diagnosis_json())]}
    assert result.duration_ms >= 0


def test_observers_see_each_model_and_tool_call() -> None:
    events: list[AgentEvent] = []
    model = FakeModel(reply(tool_use("get_referral", referral_id="GR-1")), answer())
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
        reply(tool_use("get_referral", referral_id="GR-777")), answer(summary="the final answer")
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


def test_ids_in_tool_errors_are_not_evidence() -> None:
    tools = FakeToolbox(
        {"get_referral": lambda args: ToolOutcome(text="NOT_FOUND: GR-99999", is_error=True)}
    )
    model = FakeModel(reply(tool_use("get_referral")), cited(["GR-99999"]), cited(["GR-99999"]))
    result = run(model, tools)

    assert result.dropped_evidence == ["GR-99999"]


def test_the_prompt_follows_the_tools_the_run_has() -> None:
    model = FakeModel(answer())
    tools = FakeToolbox(
        {
            "get_referral": lambda args: ToolOutcome(text="{}"),
            "search_knowledge": lambda args: ToolOutcome(text="{}"),
            "get_runbook": lambda args: ToolOutcome(text="{}"),
        }
    )
    run(model, tools)
    assert "search_knowledge" in model.requests[0].system


def test_the_run_records_the_prompt_version_it_used() -> None:
    model = FakeModel(answer())
    result = run(model, toolbox(), prompt_version="v2")

    assert result.prompt_version == "v2"
    assert "7 days" in model.requests[0].system


def test_identifiers_in_the_question_never_reach_the_model() -> None:
    model = FakeModel(answer())
    result = asyncio.run(
        run_agent("Patient SIM0042 says GR-1 never arrived", model=model, toolbox=toolbox())
    )

    assert model.requests[0].messages[0]["content"] == "Patient [REDACTED] says GR-1 never arrived"
    assert result.question == "Patient [REDACTED] says GR-1 never arrived"
    assert result.question_redactions == 1


def test_clinical_advice_gets_a_repair_turn() -> None:
    model = FakeModel(
        answer(draft_customer_reply="The patient should see a cardiologist urgently."),
        answer(draft_customer_reply="We will resend the referral today."),
    )
    result = run(model, toolbox())

    assert "clinical advice" in model.requests[1].messages[-1]["content"][0]["text"]
    assert result.outcome == "answered"
    assert result.safety_flags == []
    assert result.diagnosis is not None
    assert result.diagnosis.draft_customer_reply == "We will resend the referral today."


def test_clinical_advice_that_survives_the_repair_is_removed_from_the_draft() -> None:
    advice = "The patient should see a cardiologist urgently."
    model = FakeModel(answer(draft_customer_reply=advice), answer(draft_customer_reply=advice))
    result = run(model, toolbox())

    assert result.outcome == "answered"
    assert result.diagnosis is not None
    assert result.diagnosis.draft_customer_reply is None
    assert result.safety_flags
    assert result.safety_flags[0].startswith("draft_customer_reply:")


def test_identifiers_in_the_answer_are_masked_in_the_result() -> None:
    model = FakeModel(answer(summary="Patient SIM0042 was not affected."))
    result = run(model, toolbox())

    assert result.diagnosis is not None
    assert result.diagnosis.summary == "Patient [REDACTED] was not affected."
    assert result.output_redactions == 1
