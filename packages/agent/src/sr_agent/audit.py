"""The audit log: every diagnosis and the feedback on it (DESIGN.md section 7.4, T5.3).

`PostgresAudit` writes to the `audit` schema as a member of the sr_auditor role, which can
only insert into and read those tables. `MemoryAudit` keeps the same records in memory, for
tests and for running the HTTP API without an audit database.

A record keeps what the design lists: the (masked) question, every tool call with its
timing and result size, the answer, the models, tokens, cost and latency, and feedback. It
does not keep the transcript, which holds whole tool results; a run read back from the
audit has an empty transcript.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from sr_agent.loop import AgentRun
from sr_db.domain import FailureCode


class UnknownDiagnosis(Exception):  # noqa: N818 - reads naturally at raise sites
    pass


class Feedback(BaseModel):
    rating: Literal["UP", "DOWN"]
    correct_code: FailureCode | None = None
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackRecord(Feedback):
    user: str
    created_at: datetime


class AuditRecord(BaseModel):
    id: str
    user: str
    created_at: datetime
    run: AgentRun  # without its transcript
    feedback: list[FeedbackRecord]


class HistoryItem(BaseModel):
    id: str
    created_at: datetime
    question: str
    outcome: str
    root_cause_code: str | None
    confidence: str | None
    summary: str | None


class AuditStore(Protocol):
    def record(self, run: AgentRun, *, user: str) -> str: ...

    def get(self, diagnosis_id: str) -> AuditRecord | None: ...

    def history(self, user: str, *, limit: int) -> list[HistoryItem]: ...

    def add_feedback(self, diagnosis_id: str, feedback: Feedback, *, user: str) -> None:
        """Raises UnknownDiagnosis for an id that was never recorded."""
        ...


def diagnosis_id(run: AgentRun) -> str:
    return f"dx_{run.run_id}"


def _history_item(record: AuditRecord) -> HistoryItem:
    d = record.run.diagnosis
    return HistoryItem(
        id=record.id,
        created_at=record.created_at,
        question=record.run.question,
        outcome=record.run.outcome,
        root_cause_code=d.root_cause_code.value if d and d.root_cause_code else None,
        confidence=d.confidence if d else None,
        summary=d.summary if d else None,
    )


class MemoryAudit:
    def __init__(self) -> None:
        self._records: dict[str, AuditRecord] = {}

    def record(self, run: AgentRun, *, user: str) -> str:
        key = diagnosis_id(run)
        self._records[key] = AuditRecord(
            id=key,
            user=user,
            created_at=datetime.now(UTC),
            run=run.model_copy(update={"transcript": []}),
            feedback=[],
        )
        return key

    def get(self, diagnosis_id: str) -> AuditRecord | None:
        return self._records.get(diagnosis_id)

    def history(self, user: str, *, limit: int) -> list[HistoryItem]:
        mine = [r for r in self._records.values() if r.user == user]
        return [_history_item(r) for r in reversed(mine)][:limit]

    def add_feedback(self, diagnosis_id: str, feedback: Feedback, *, user: str) -> None:
        record = self._records.get(diagnosis_id)
        if record is None:
            raise UnknownDiagnosis(diagnosis_id)
        record.feedback.append(
            FeedbackRecord(**feedback.model_dump(), user=user, created_at=datetime.now(UTC))
        )


_INSERT = """
INSERT INTO audit.diagnosis (
    id, user_ref, question, model, served_by, prompt_version, outcome, diagnosis, tool_calls,
    turns, repairs, dropped_evidence, question_redactions, output_redactions, safety_flags,
    input_tokens, cache_write_tokens, cache_read_tokens, output_tokens, turn_usage, cost_usd,
    latency_ms, created_at)
VALUES (%(id)s, %(user)s, %(question)s, %(model)s, %(served_by)s, %(prompt_version)s,
    %(outcome)s, %(diagnosis)s, %(tool_calls)s, %(turns)s, %(repairs)s, %(dropped)s,
    %(question_redactions)s, %(output_redactions)s, %(safety_flags)s, %(input)s,
    %(cache_write)s, %(cache_read)s, %(output)s, %(turn_usage)s, %(cost)s, %(latency)s,
    %(created_at)s)
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


class PostgresAudit:
    def __init__(self, url: str) -> None:
        self._url = url

    def close(self) -> None:
        return None  # one short connection per call; nothing to close

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self._url, autocommit=True, row_factory=dict_row)

    def record(self, run: AgentRun, *, user: str) -> str:
        key = diagnosis_id(run)
        data = run.model_dump(mode="json")
        with self._connect() as conn:
            conn.execute(
                _INSERT,
                {
                    "id": key,
                    "user": user,
                    "question": run.question,
                    "model": run.model,
                    "served_by": _json(run.served_by),
                    "prompt_version": run.prompt_version,
                    "outcome": run.outcome,
                    "diagnosis": _json(data["diagnosis"]) if run.diagnosis else None,
                    "tool_calls": _json(data["tool_calls"]),
                    "turns": run.turns,
                    "repairs": run.repairs,
                    "dropped": _json(run.dropped_evidence),
                    "question_redactions": run.question_redactions,
                    "output_redactions": run.output_redactions,
                    "safety_flags": _json(run.safety_flags),
                    "input": run.usage.input_tokens,
                    "cache_write": run.usage.cache_creation_input_tokens,
                    "cache_read": run.usage.cache_read_input_tokens,
                    "output": run.usage.output_tokens,
                    "turn_usage": _json(data["turn_usage"]),
                    "cost": run.cost_usd,
                    "latency": run.duration_ms,
                    "created_at": datetime.now(UTC),
                },
            )
        return key

    def get(self, diagnosis_id: str) -> AuditRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM audit.diagnosis WHERE id = %s", (diagnosis_id,)
            ).fetchone()
            if row is None:
                return None
            feedback = conn.execute(
                "SELECT rating, correct_code, comment, user_ref AS user, created_at"
                " FROM audit.feedback WHERE diagnosis_id = %s ORDER BY id",
                (diagnosis_id,),
            ).fetchall()
        run = AgentRun(
            run_id=row["id"].removeprefix("dx_"),
            question=row["question"],
            diagnosis=row["diagnosis"],
            outcome=row["outcome"],
            model=row["model"],
            served_by=row["served_by"],
            prompt_version=row["prompt_version"],
            turns=row["turns"],
            repairs=row["repairs"],
            dropped_evidence=row["dropped_evidence"],
            question_redactions=row["question_redactions"],
            output_redactions=row["output_redactions"],
            safety_flags=row["safety_flags"],
            tool_calls=row["tool_calls"],
            usage={
                "input_tokens": row["input_tokens"],
                "cache_creation_input_tokens": row["cache_write_tokens"],
                "cache_read_input_tokens": row["cache_read_tokens"],
                "output_tokens": row["output_tokens"],
            },
            turn_usage=row["turn_usage"],
            duration_ms=float(row["latency_ms"]),
            transcript=[],
        )
        return AuditRecord(
            id=row["id"],
            user=row["user_ref"],
            created_at=row["created_at"],
            run=run,
            feedback=[FeedbackRecord(**f) for f in feedback],
        )

    def history(self, user: str, *, limit: int) -> list[HistoryItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, question, outcome,"
                " diagnosis ->> 'root_cause_code' AS root_cause_code,"
                " diagnosis ->> 'confidence' AS confidence, diagnosis ->> 'summary' AS summary"
                " FROM audit.diagnosis WHERE user_ref = %s ORDER BY created_at DESC, id DESC"
                " LIMIT %s",
                (user, limit),
            ).fetchall()
        return [HistoryItem(**row) for row in rows]

    def add_feedback(self, diagnosis_id: str, feedback: Feedback, *, user: str) -> None:
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO audit.feedback"
                    " (diagnosis_id, user_ref, rating, correct_code, comment, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        diagnosis_id,
                        user,
                        feedback.rating,
                        feedback.correct_code.value if feedback.correct_code else None,
                        feedback.comment,
                        datetime.now(UTC),
                    ),
                )
            except psycopg.errors.ForeignKeyViolation as error:
                raise UnknownDiagnosis(diagnosis_id) from error
