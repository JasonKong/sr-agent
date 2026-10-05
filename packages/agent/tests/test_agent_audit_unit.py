"""The in-memory audit store, which the HTTP API uses when no audit database is set."""

import asyncio

import pytest

from sr_agent.audit import Feedback, MemoryAudit, UnknownDiagnosis
from sr_agent.loop import AgentRun, run_agent
from sr_agent.testing import FakeModel, FakeToolbox, answer


def a_run(question: str = "Why?") -> AgentRun:
    return asyncio.run(
        run_agent(
            question, model=FakeModel(answer()), toolbox=FakeToolbox({}), observe=lambda e: None
        )
    )


def test_a_recorded_run_comes_back_by_id() -> None:
    audit = MemoryAudit()
    run = a_run()
    diagnosis_id = audit.record(run, user="demo")

    assert diagnosis_id == f"dx_{run.run_id}"
    record = audit.get(diagnosis_id)
    assert record is not None
    assert record.run == run.model_copy(update={"transcript": []})  # not kept
    assert record.feedback == []
    assert audit.get("dx_000000000000") is None


def test_history_is_per_user_and_newest_first() -> None:
    audit = MemoryAudit()
    first = audit.record(a_run("first"), user="demo")
    audit.record(a_run("someone else's"), user="other")
    second = audit.record(a_run("second"), user="demo")

    history = audit.history("demo", limit=10)
    assert [h.id for h in history] == [second, first]
    assert history[0].question == "second"
    assert history[0].root_cause_code == "F07_CLINIC_PMS_AUTH_FAILED"
    assert len(audit.history("demo", limit=1)) == 1


def test_feedback_is_kept_with_its_diagnosis() -> None:
    audit = MemoryAudit()
    diagnosis_id = audit.record(a_run(), user="demo")
    audit.add_feedback(
        diagnosis_id, Feedback(rating="DOWN", correct_code="F08_CLINIC_PMS_TIMEOUT"), user="demo"
    )

    record = audit.get(diagnosis_id)
    assert record is not None
    assert [(f.rating, f.correct_code) for f in record.feedback] == [
        ("DOWN", "F08_CLINIC_PMS_TIMEOUT")
    ]
    with pytest.raises(UnknownDiagnosis):
        audit.add_feedback("dx_000000000000", Feedback(rating="UP"), user="demo")
