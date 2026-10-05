"""The audit schema: only the auditor role writes and reads it, and it can do nothing else."""

import psycopg
import pytest
from psycopg import errors, sql

from sr_db.cli import main
from sr_db.testing import READER_PASSWORD

pytestmark = pytest.mark.integration

AUDITOR = "sr_test_auditor"
ROW = """
INSERT INTO audit.diagnosis (
    id, user_ref, question, model, served_by, prompt_version, outcome, diagnosis, tool_calls,
    turns, repairs, dropped_evidence, question_redactions, output_redactions, safety_flags,
    input_tokens, cache_write_tokens, cache_read_tokens, output_tokens, turn_usage, cost_usd,
    latency_ms, created_at)
VALUES ('dx_0123456789ab', 'demo', 'Why?', 'm', '[]', 'v1', 'answered', '{}', '[]',
    2, 0, '[]', 0, 0, '[]', 1, 2, 3, 4, '[]', 0.01, 1200.5, '2026-10-05T00:00:00Z')
"""


@pytest.fixture
def as_auditor(migrated: psycopg.Connection, migrated_url: str) -> psycopg.Connection:
    exists = migrated.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (AUDITOR,)).fetchone()
    verb = "ALTER" if exists else "CREATE"
    migrated.execute(
        sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(
            sql.SQL(verb), sql.Identifier(AUDITOR), sql.Literal(READER_PASSWORD)
        )
    )
    migrated.execute(sql.SQL("GRANT sr_auditor TO {}").format(sql.Identifier(AUDITOR)))
    host_and_db = migrated_url.split("@", 1)[1]
    return psycopg.connect(
        f"postgresql://{AUDITOR}:{READER_PASSWORD}@{host_and_db}", autocommit=True
    )


def test_the_auditor_writes_and_reads_diagnoses_and_feedback(
    as_auditor: psycopg.Connection,
) -> None:
    with as_auditor:
        as_auditor.execute(ROW)
        as_auditor.execute(
            "INSERT INTO audit.feedback (diagnosis_id, user_ref, rating, correct_code, created_at)"
            " VALUES ('dx_0123456789ab', 'demo', 'DOWN', 'F08_CLINIC_PMS_TIMEOUT', now())"
        )
        row = as_auditor.execute("SELECT rating, correct_code FROM audit.feedback").fetchone()
        assert row == ("DOWN", "F08_CLINIC_PMS_TIMEOUT")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit.diagnosis SET outcome = 'refused'",
        "DELETE FROM audit.diagnosis",
        "SELECT id FROM composer.referral",
        "SELECT id FROM knowledge.support_ticket",
    ],
)
def test_the_auditor_can_do_nothing_else(as_auditor: psycopg.Connection, statement: str) -> None:
    with as_auditor, pytest.raises(errors.InsufficientPrivilege):
        as_auditor.execute(statement.encode())


def test_the_mcp_reader_cannot_read_the_audit(reader_url: str) -> None:
    with (
        psycopg.connect(reader_url, autocommit=True) as conn,
        pytest.raises(errors.InsufficientPrivilege),
    ):
        conn.execute("SELECT * FROM audit.diagnosis")


def test_feedback_must_name_a_diagnosis_and_a_valid_rating(migrated: psycopg.Connection) -> None:
    with pytest.raises(errors.ForeignKeyViolation), migrated.transaction():
        migrated.execute(
            "INSERT INTO audit.feedback (diagnosis_id, user_ref, rating, created_at)"
            " VALUES ('dx_ffffffffffff', 'demo', 'UP', now())"
        )
    migrated.execute(ROW)
    with pytest.raises(errors.CheckViolation), migrated.transaction():
        migrated.execute(
            "INSERT INTO audit.feedback (diagnosis_id, user_ref, rating, created_at)"
            " VALUES ('dx_0123456789ab', 'demo', 'MEH', now())"
        )


def test_create_auditor_makes_a_login_that_can_only_audit(
    migrated_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    host_and_db = migrated_url.split("@", 1)[1]
    auditor_url = f"postgresql://sr_cli_auditor:cli-test-password@{host_and_db}"
    monkeypatch.setenv("DATABASE_URL", migrated_url)
    monkeypatch.setenv("SRA_AUDIT_DATABASE_URL", auditor_url)

    assert main(["create-auditor"]) == 0
    assert main(["create-auditor"]) == 0
    assert "sr_cli_auditor" in capsys.readouterr().out

    with psycopg.connect(auditor_url, autocommit=True) as auditor:
        auditor.execute(ROW)
        assert auditor.execute("SELECT count(*) FROM audit.diagnosis").fetchone() == (1,)
        with pytest.raises(errors.InsufficientPrivilege):
            auditor.execute("SELECT count(*) FROM hub.clinic")
