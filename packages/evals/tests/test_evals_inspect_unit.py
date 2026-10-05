"""`sr-evals inspect`: what each stored run shows about thinking, caching and its turns."""

import asyncio
import json
from pathlib import Path

import pytest

from sr_agent.loop import AgentRun, Limits, run_agent
from sr_agent.model import Block, ModelReply, TokenUsage
from sr_agent.testing import FakeModel, FakeToolbox, answer, reply, thinking, tool_use
from sr_agent.toolbox import ToolOutcome
from sr_evals.inspect import check_run, render_checks


def tools() -> FakeToolbox:
    return FakeToolbox({"get_referral": lambda args: ToolOutcome(text='{"id": "GR-1"}')})


def run(model: FakeModel, max_tool_calls: int = 8) -> AgentRun:
    limits = Limits(max_tool_calls=max_tool_calls)
    return asyncio.run(run_agent("Why?", model=model, toolbox=tools(), limits=limits))


def turn(*blocks: Block, read: int, model: str = "claude-opus-5-5") -> ModelReply:
    return reply(*blocks, usage=TokenUsage(cache_read_input_tokens=read), model=model)


def test_a_healthy_run_replays_thinking_and_reads_the_cache_from_its_second_turn() -> None:
    model = FakeModel(
        turn(thinking("Look it up first."), tool_use("get_referral", referral_id="GR-1"), read=0),
        turn(thinking(""), *answer().content, read=6_000),
    )
    check = check_run("F07-1", run(model), requested="claude-opus-5-5")

    assert check.turns == 2
    assert check.tools == ["get_referral"]
    assert (check.thinking_blocks, check.thinking_with_text) == (2, 1)
    assert check.cache_read_by_turn == [0, 6_000]
    assert check.later_turns_read_cache is True
    assert check.first_turn_read_cache is False
    assert check.structured_with_tools is True
    assert (check.repairs, check.wrap_up, check.fallback) == (0, False, False)


def test_a_later_turn_without_a_cache_read_is_flagged() -> None:
    model = FakeModel(
        turn(tool_use("get_referral", referral_id="GR-1"), read=0),
        turn(tool_use("get_referral", referral_id="GR-1"), read=6_000),
        turn(*answer().content, read=0),  # something changed the prefix
    )
    check = check_run("F07-1", run(model), requested="claude-opus-5-5")

    assert check.later_turns_read_cache is False


def test_a_turn_served_by_another_model_is_a_fallback_but_a_dated_alias_is_not() -> None:
    fallback = FakeModel(
        turn(tool_use("get_referral", referral_id="GR-1"), read=0),
        turn(*answer().content, read=10, model="claude-opus-5"),
    )
    assert check_run("x", run(fallback), requested="claude-opus-5-5").fallback is True

    dated = FakeModel(turn(*answer().content, read=0, model="claude-haiku-4-5-20251001"))
    assert check_run("x", run(dated), requested="claude-haiku-4-5").fallback is False


def test_a_limit_with_a_partial_answer_is_a_wrap_up() -> None:
    model = FakeModel(
        turn(tool_use("get_referral", id="t1"), read=0),
        turn(tool_use("get_referral", id="t2"), read=10),
        turn(*answer(confidence="low").content, read=10),
    )
    check = check_run("F07-1", run(model, max_tool_calls=1), requested="claude-opus-5-5")

    assert check.outcome == "tool_limit"
    assert check.wrap_up is True


def test_the_table_has_a_row_per_run_and_the_totals() -> None:
    model = FakeModel(
        turn(thinking("x"), tool_use("get_referral", referral_id="GR-1"), read=0),
        turn(*answer().content, read=6_000),
    )
    text_out = render_checks([check_run("F07-1", run(model), requested="claude-opus-5-5")])

    assert "| F07-1 | answered | 2 | 1 |" in text_out
    assert "Every later turn read the cache: 1/1 runs." in text_out
    assert "Structured answer after tool calls: 1/1." in text_out


def test_inspect_reads_a_reports_stored_runs_and_a_single_run_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from sr_evals.cli import main

    model = FakeModel(
        turn(thinking("x"), tool_use("get_referral", referral_id="GR-1"), read=0),
        turn(*answer().content, read=6_000),
    )
    stored = run(model).model_dump(mode="json")
    runs = tmp_path / "runs" / "r1"
    runs.mkdir(parents=True)
    (runs / "F07-1.json").write_text(json.dumps(stored))
    (runs / "F06-1.json").write_text(json.dumps({"error": "ModelUnavailable: 529"}))

    assert main(["inspect", "r1", "--runs-dir", str(tmp_path / "runs")]) == 0
    out = capsys.readouterr().out
    assert "| F07-1 | answered |" in out
    assert "F06-1: ModelUnavailable: 529" in out

    single = tmp_path / "ask.json"  # what `sra ask --json` prints
    single.write_text(json.dumps(stored))
    assert main(["inspect", str(single)]) == 0
    assert "| ask | answered |" in capsys.readouterr().out
