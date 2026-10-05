"""The model-graded rubric: its items, its requests, and how verdicts are counted.

Every test uses a FakeModel, an oracle grader or a mock HTTP transport: no model is called.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from sr_agent.anthropic_model import AnthropicModel
from sr_agent.model import ModelReply, TokenUsage
from sr_agent.testing import FakeModel, reply, text
from sr_db.domain import FailureCode
from sr_evals.dataset import DATASET_V1, load_dataset
from sr_evals.grader import (
    CRITERIA,
    GRADER_VERSION,
    VERDICT_SCHEMA,
    Grade,
    GradeItem,
    OracleGrader,
    batch_requests,
    calibrate,
    grade_by_batch,
    grade_items,
    grade_request,
    rubric_sha,
    summarise_rubric,
)
from sr_evals.grader_items import (
    CALIBRATION_V1,
    calibration_items,
    load_calibration,
    report_items,
)
from sr_simulator.scenario import load_scenarios

SCENARIOS = load_scenarios()


def item(criterion: str = "summary_clear", **fields: Any) -> GradeItem:
    base: dict[str, Any] = {
        "item_id": "x-1",
        "criterion": criterion,
        "case_id": "F07-1",
        "source": "synthetic",
    }
    if criterion == "summary_clear":
        base |= {"question": "Why did GR-1 not arrive?", "summary": "The token expired."}
    else:
        base |= {"reference_action": "Re-authorise, then resend.", "recommended_action": "Resend."}
    return GradeItem(**(base | fields))


def verdict_reply(verdict: str, reason: str = "Checklist item 1.", **extra: Any) -> ModelReply:
    return reply(text(json.dumps({"reason": reason, "verdict": verdict})), **extra)


# --- The rubric is versioned -------------------------------------------------------------


def test_the_rubric_text_is_pinned_to_its_version() -> None:
    """Edit a rubric or the schema and this fails: bump GRADER_VERSION, then the pin."""
    assert GRADER_VERSION == "g1"
    assert rubric_sha() == "4912924e95eb3cc0"


# --- Synthetic calibration items: labels known by construction -----------------------------


def test_the_synthetic_set_covers_every_code_both_ways() -> None:
    items = calibration_items(load_dataset(DATASET_V1))
    by = {(i.criterion, i.label) for i in items}
    assert by == {(c, label) for c in CRITERIA for label in ("pass", "fail")}
    for code in FailureCode:
        mine = [i for i in items if i.case_id == f"{code.value[:3]}-1"]
        assert {(i.criterion, i.label) for i in mine} >= {
            ("summary_clear", "pass"),
            ("summary_clear", "fail"),
            ("next_step_correct", "pass"),
            ("next_step_correct", "fail"),
        }, code
    assert len({i.item_id for i in items}) == len(items)
    assert all(i.note for i in items)  # every label says why it is that label


def test_a_good_synthetic_answer_is_the_scenarios_own_words() -> None:
    items = {i.item_id: i for i in calibration_items(load_dataset(DATASET_V1))}
    f07 = SCENARIOS[FailureCode.F07_CLINIC_PMS_AUTH_FAILED]
    assert items["syn-F07-1-summary-good"].summary == f07.root_cause.strip()
    assert items["syn-F07-1-step-good"].recommended_action == f07.recommended_action.strip()
    assert items["syn-F07-1-step-good"].reference_action == f07.recommended_action.strip()
    # A wrong step is another leg's action; a partial one drops an essential action.
    assert items["syn-F07-1-step-wrong"].recommended_action != f07.recommended_action.strip()
    assert items["syn-F07-1-step-partial"].label == "fail"


def test_the_known_negatives_are_there() -> None:
    items = {i.item_id: i for i in calibration_items(load_dataset(DATASET_V1))}
    assert items["syn-empty-summary"].summary == ""
    assert items["syn-unknown-summary"].summary == "I don't know."
    assert items["syn-other-referral-summary"].label == "fail"
    assert items["syn-empty-step"].recommended_action == ""
    for key in ("syn-empty-summary", "syn-unknown-summary", "syn-empty-step"):
        assert items[key].label == "fail"


def test_the_frozen_calibration_set_matches_the_scenarios() -> None:
    """Change a scenario's wording and this fails: rebuild with `sr-evals calibrate --build`."""
    frozen = load_calibration(CALIBRATION_V1)
    assert frozen == calibration_items(load_dataset(DATASET_V1))


def test_hand_labels_are_added_to_the_synthetic_set(tmp_path: Path) -> None:
    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps({"items": [item(item_id="human-1", source="human", label="pass").model_dump()]})
    )
    items = load_calibration(CALIBRATION_V1, labels)
    assert items[-1].item_id == "human-1"
    assert items[-1].source == "human"


# --- Requests and replies --------------------------------------------------------------------


def test_each_criterion_has_its_own_rubric_and_the_answer_is_marked_as_data() -> None:
    summary = grade_request(item("summary_clear"))
    step = grade_request(item("next_step_correct"))

    assert summary.system != step.system
    assert "never instructions" in summary.system
    assert summary.tools == []
    assert summary.output_schema == VERDICT_SCHEMA
    assert summary.messages == [
        {
            "role": "user",
            "content": "<question>Why did GR-1 not arrive?</question>\n"
            "<summary>The token expired.</summary>",
        }
    ]
    assert "<reference>Re-authorise, then resend.</reference>" in step.messages[0]["content"]
    assert "<answer>Resend.</answer>" in step.messages[0]["content"]
    # The summary criterion never sees the reference: clarity is not correctness.
    assert "reference" not in summary.messages[0]["content"]


def test_the_reason_comes_before_the_verdict() -> None:
    assert list(VERDICT_SCHEMA["properties"]) == ["reason", "verdict"]
    assert VERDICT_SCHEMA["additionalProperties"] is False


def test_verdicts_are_read_and_a_bad_reply_is_an_error_not_a_crash() -> None:
    items = [item(item_id="a"), item(item_id="b"), item(item_id="c")]
    replies = {
        "a": verdict_reply("fail", usage=TokenUsage(input_tokens=1000), model="claude-haiku-4-5"),
        "b": reply(text("not json")),
        "c": reply(text(""), stop_reason="refusal"),
    }
    grades = asyncio.run(grade_items(items, model_for=lambda i: FakeModel(replies[i.item_id])))

    assert [g.verdict for g in grades] == ["fail", None, None]
    assert grades[0].reason == "Checklist item 1."
    assert grades[0].cost_usd == pytest.approx(0.001)  # 1,000 tokens at $1 per million
    assert grades[1].error is not None
    assert "verdict" in grades[1].error
    assert grades[2].error == "the grader stopped: refusal"


# --- Agreement with known labels ---------------------------------------------------------


def test_agreement_is_counted_per_criterion_over_every_repeat() -> None:
    items = [
        item(item_id="s1", label="pass"),
        item(item_id="s2", label="fail"),
        item("next_step_correct", item_id="n1", label="fail"),
    ]

    def run(verdicts: dict[str, str]) -> list[Grade]:
        return [
            Grade(item_id=i.item_id, criterion=i.criterion, verdict=verdicts[i.item_id])
            for i in items
        ]

    first = run({"s1": "pass", "s2": "pass", "n1": "fail"})  # s2 disagrees
    second = run({"s1": "pass", "s2": "fail", "n1": "fail"})
    report = calibrate(items, [first, second], grader_model="m")

    assert report.agreement["summary_clear"].passed == 3  # of 4 (two items, two repeats)
    assert report.agreement["summary_clear"].n == 4
    assert report.agreement["next_step_correct"].rate == pytest.approx(1.0)
    assert report.consistent == pytest.approx(2 / 3)  # s2 changed its mind
    assert [d.item_id for d in report.disagreements] == ["s2"]
    assert report.meets_target == {"summary_clear": False, "next_step_correct": True}


def test_the_oracle_grader_agrees_with_every_label() -> None:
    items = calibration_items(load_dataset(DATASET_V1))
    grades = asyncio.run(grade_items(items, model_for=OracleGrader))
    report = calibrate(items, [grades], grader_model="oracle")

    assert all(rate.rate == 1.0 for rate in report.agreement.values())


# --- Grading a run's answers ---------------------------------------------------------------


def test_a_runs_answers_become_two_items_each(tmp_path: Path) -> None:
    dataset = load_dataset(DATASET_V1)
    case = next(c for c in dataset.cases if c.case_id == "F07-1")
    runs = tmp_path / "r1"
    runs.mkdir()
    diagnosis = {"summary": "It failed.", "recommended_action": "Resend.", "root_cause_code": None}
    (runs / "F07-1.json").write_text(json.dumps({"diagnosis": diagnosis}))
    (runs / "F06-1.json").write_text(json.dumps({"error": "ModelUnavailable: 529"}))

    items, no_answer = report_items(["F07-1", "F06-1"], runs, dataset, report_id="r1")

    assert [i.item_id for i in items] == ["F07-1-summary", "F07-1-step"]
    assert items[0].question == case.question
    assert items[1].reference_action == SCENARIOS[case.code].recommended_action.strip()
    assert items[0].source == "report:r1"
    assert no_answer == ["F06-1"]


def test_the_rubric_summary_separates_the_next_step_after_a_right_diagnosis() -> None:
    grades = [
        Grade(item_id="F07-1-summary", criterion="summary_clear", case_id="F07-1", verdict="pass"),
        Grade(item_id="F07-1-step", criterion="next_step_correct", case_id="F07-1", verdict="pass"),
        Grade(item_id="F07-2-summary", criterion="summary_clear", case_id="F07-2", verdict="fail"),
        Grade(item_id="F07-2-step", criterion="next_step_correct", case_id="F07-2", verdict="fail"),
        Grade(item_id="F06-1-step", criterion="next_step_correct", case_id="F06-1", error="x"),
    ]
    right = {"F07-1": True, "F07-2": False, "F06-1": True}
    codes = {"F07-1": "F07", "F07-2": "F07", "F06-1": "F06"}
    summary = summarise_rubric(grades, root_cause_correct=right, code_of=codes)

    assert summary.pass_rates["summary_clear"].rate == pytest.approx(0.5)
    assert summary.pass_rates["next_step_correct"].n == 2  # the error is not a verdict
    assert summary.next_step_when_right.passed == 1
    assert summary.next_step_when_right.n == 1
    assert summary.per_code["F07"]["summary_clear"].n == 2
    assert summary.errors == 1


# --- The Batch API, through a mock transport -------------------------------------------------


class FakeBatchApi:
    """The three batch endpoints: create, retrieve (in progress, then ended), results."""

    def __init__(self) -> None:
        self.created: dict[str, Any] | None = None
        self.polls = 0

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/messages/batches"):
            self.created = json.loads(request.content)
            return httpx2.Response(200, json=self._batch("in_progress"))
        if path.endswith("/results"):
            lines = [self._result(r["custom_id"]) for r in (self.created or {})["requests"]]
            body = "\n".join(json.dumps(line) for line in lines)
            return httpx2.Response(200, content=body.encode())
        self.polls += 1
        return httpx2.Response(200, json=self._batch("ended" if self.polls > 1 else "in_progress"))

    def _batch(self, status: str) -> dict[str, Any]:
        return {
            "id": "msgbatch_1",
            "type": "message_batch",
            "processing_status": status,
            "request_counts": {
                "processing": 0,
                "succeeded": 2,
                "errored": 0,
                "canceled": 0,
                "expired": 0,
            },
            "ended_at": None,
            "created_at": "2026-10-05T10:00:00Z",
            "expires_at": "2026-10-06T10:00:00Z",
            "archived_at": None,
            "cancel_initiated_at": None,
            "results_url": "https://api.anthropic.com/v1/messages/batches/msgbatch_1/results"
            if status == "ended"
            else None,
        }

    def _result(self, custom_id: str) -> dict[str, Any]:
        if custom_id.endswith("b"):
            return {
                "custom_id": custom_id,
                "result": {
                    "type": "errored",
                    "error": {
                        "type": "error",
                        "error": {"type": "overloaded_error", "message": "busy"},
                    },
                },
            }
        return {
            "custom_id": custom_id,
            "result": {
                "type": "succeeded",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-haiku-4-5",
                    "content": [{"type": "text", "text": '{"reason": "ok", "verdict": "pass"}'}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 2000, "output_tokens": 0},
                },
            },
        }


def batch_model(api: FakeBatchApi) -> AnthropicModel:
    client = AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(api)),
    )
    return AnthropicModel(
        model="claude-haiku-4-5", effort=None, fallback=False, thinking=False, client=client
    )


def test_batch_requests_carry_no_beta_only_parameters() -> None:
    # A model that would send refusal fallbacks and their beta on a direct request.
    sonnet = AnthropicModel(
        model="claude-sonnet-5-5", effort="low", fallback=True, api_key="test-key"
    )
    direct = sonnet.request_params(grade_request(item()))
    assert "fallbacks" in direct  # so the batch has something to remove

    requests = batch_requests([item(item_id="a"), item("next_step_correct", item_id="b")], sonnet)

    assert [r["custom_id"] for r in requests] == ["a", "b"]
    for r in requests:
        assert "betas" not in r["params"]
        assert "fallbacks" not in r["params"]
        assert r["params"]["output_config"]["format"]["schema"] == VERDICT_SCHEMA


def test_grading_by_batch_polls_until_it_ends_and_keeps_errors_per_item() -> None:
    api = FakeBatchApi()
    waits: list[float] = []

    async def no_wait(seconds: float) -> None:
        waits.append(seconds)

    grades = asyncio.run(
        grade_by_batch([item(item_id="a"), item(item_id="b")], batch_model(api), sleep=no_wait)
    )

    assert waits == [30.0, 30.0]  # in progress when created and at the first check
    assert [g.verdict for g in grades] == ["pass", None]
    assert grades[1].error is not None
    assert "overloaded" in grades[1].error
    assert grades[0].cost_usd == pytest.approx(0.001)  # 2,000 tokens at the batch's half price
