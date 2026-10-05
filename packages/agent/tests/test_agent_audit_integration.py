"""The Postgres audit store, through the least-privilege auditor role."""

import asyncio

import psycopg
import pytest
from psycopg import sql

from sr_agent.audit import Feedback, PostgresAudit
from sr_agent.loop import AgentRun, run_agent
from sr_agent.model import TokenUsage
from sr_agent.testing import FakeModel, FakeToolbox, answer, reply, tool_use
from sr_agent.toolbox import ToolOutcome
from sr_db.testing import READER_PASSWORD

pytestmark = pytest.mark.integration

AUDITOR = "sr_test_agent_auditor"


@pytest.fixture
def auditor_url(migrated: psycopg.Connection, migrated_url: str) -> str:
    exists = migrated.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (AUDITOR,)).fetchone()
    verb = "ALTER" if exists else "CREATE"
    migrated.execute(
        sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(
            sql.SQL(verb), sql.Identifier(AUDITOR), sql.Literal(READER_PASSWORD)
        )
    )
    migrated.execute(sql.SQL("GRANT sr_auditor TO {}").format(sql.Identifier(AUDITOR)))
    return f"postgresql://{AUDITOR}:{READER_PASSWORD}@{migrated_url.split('@', 1)[1]}"


def a_run(question: str) -> AgentRun:
    model = FakeModel(
        reply(tool_use("get_referral", referral_id="GR-1"), usage=TokenUsage(input_tokens=10)),
        answer(evidence=[("GR-1", "the referral")]),
    )
    tools = FakeToolbox({"get_referral": lambda args: ToolOutcome(text='{"id":"GR-1"}')})
    return asyncio.run(run_agent(question, model=model, toolbox=tools, observe=lambda e: None))


def test_runs_round_trip_with_history_and_feedback(
    auditor_url: str, migrated: psycopg.Connection
) -> None:
    audit = PostgresAudit(auditor_url)
    try:
        first = audit.record(a_run("Patient SIM0042: why?"), user="demo")
        second = audit.record(a_run("And this one?"), user="demo")
        audit.add_feedback(second, Feedback(rating="UP", comment="spot on"), user="demo")

        record = audit.get(second)
        assert record is not None
        assert record.run.diagnosis is not None
        assert record.run.tool_calls[0].name == "get_referral"
        assert [(f.rating, f.comment) for f in record.feedback] == [("UP", "spot on")]
        assert [h.id for h in audit.history("demo", limit=10)] == [second, first]
        assert audit.get("dx_000000000000") is None
    finally:
        audit.close()

    stored = migrated.execute("SELECT question, input_tokens FROM audit.diagnosis").fetchall()
    assert ("Patient [REDACTED]: why?", 10) in stored  # masked before it was ever stored
