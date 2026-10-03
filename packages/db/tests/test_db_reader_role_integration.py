"""Integration tests for the read-only role the MCP server uses (migration 0005).

Privacy is enforced by the database: the role cannot read patient names, NHIs or dates of
birth, and can only learn whether those fields are present, empty or invalid.
"""

from collections.abc import Iterator

import psycopg
import pytest
from psycopg import errors, sql

from sr_db.cli import main

pytestmark = pytest.mark.integration

PATIENT = """
INSERT INTO composer.gp_practice (id, name, pms_vendor, status, created_at)
VALUES ('prc_1', 'Totara Street Medical', 'KAURI_PMS', 'ACTIVE', now());
INSERT INTO composer.patient_snapshot
    (id, practice_id, synthetic_nhi, given_name, family_name, date_of_birth)
VALUES ('pt_1', 'prc_1', 'SIM0001', 'Aroha', 'Test', '01-02-1980'),
       ('pt_2', 'prc_1', NULL, 'Rangi', 'Test', '1980-02-01'),
       ('pt_3', 'prc_1', '', 'Mere', 'Test', NULL);
"""


@pytest.fixture
def as_reader(migrated: psycopg.Connection) -> Iterator[psycopg.Connection]:
    migrated.execute(PATIENT.encode())
    migrated.execute("SET ROLE sr_reader")
    yield migrated
    migrated.execute("RESET ROLE")


@pytest.mark.parametrize("column", ["given_name", "family_name", "synthetic_nhi", "date_of_birth"])
def test_reader_cannot_read_patient_identifiers(as_reader: psycopg.Connection, column: str) -> None:
    with pytest.raises(errors.InsufficientPrivilege), as_reader.transaction():
        as_reader.execute(
            sql.SQL("SELECT {} FROM composer.patient_snapshot").format(sql.Identifier(column))
        )


def test_reader_cannot_select_star_from_patients(as_reader: psycopg.Connection) -> None:
    with pytest.raises(errors.InsufficientPrivilege), as_reader.transaction():
        as_reader.execute("SELECT * FROM composer.patient_snapshot")


def test_reader_can_read_patient_ids(as_reader: psycopg.Connection) -> None:
    rows = as_reader.execute("SELECT id, practice_id FROM composer.patient_snapshot ORDER BY id")
    assert [r[0] for r in rows.fetchall()] == ["pt_1", "pt_2", "pt_3"]


@pytest.mark.parametrize(
    ("patient", "expected"),
    [
        ("pt_1", ("present", "present")),
        ("pt_2", ("empty", "invalid")),
        ("pt_3", ("empty", "empty")),
    ],
)
def test_reader_learns_only_field_status(
    as_reader: psycopg.Connection, patient: str, expected: tuple[str, str]
) -> None:
    row = as_reader.execute(
        "SELECT synthetic_nhi, date_of_birth FROM composer.patient_field_status(%s)", (patient,)
    ).fetchone()
    assert row == expected


def test_reader_can_read_every_other_table(migrated: psycopg.Connection) -> None:
    # A new table without a grant fails here, so the MCP server never silently loses access.
    missing = migrated.execute(
        """
        SELECT table_schema || '.' || table_name FROM information_schema.tables
        WHERE table_schema IN ('composer', 'hub', 'knowledge')
          AND table_name <> 'patient_snapshot'
          AND NOT has_table_privilege('sr_reader', table_schema || '.' || table_name, 'SELECT')
        """
    ).fetchall()
    assert missing == []


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO hub.clinic (id, name, specialty_group, status, has_active_account, created_at)"
        " VALUES ('cln_9', 'x', 'ENT', 'ACTIVE', true, now())",
        "UPDATE composer.referral SET status = 'ERROR'",
        "DELETE FROM knowledge.runbook",
        "CREATE TABLE hub.sneaky (id int)",
    ],
)
def test_reader_cannot_write(as_reader: psycopg.Connection, statement: str) -> None:
    with pytest.raises(errors.InsufficientPrivilege), as_reader.transaction():
        as_reader.execute(statement.encode())


def test_create_reader_is_idempotent_and_logs_in_read_only(
    migrated_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    host_and_db = migrated_url.split("@", 1)[1]
    reader_url = f"postgresql://sr_cli_reader:cli-test-password@{host_and_db}"
    monkeypatch.setenv("DATABASE_URL", migrated_url)
    monkeypatch.setenv("MCP_DATABASE_URL", reader_url)

    assert main(["create-reader"]) == 0
    assert main(["create-reader"]) == 0
    assert "sr_cli_reader" in capsys.readouterr().out

    with psycopg.connect(reader_url, autocommit=True) as reader:
        assert reader.execute("SELECT count(*) FROM hub.clinic").fetchone() == (0,)
        with pytest.raises(errors.InsufficientPrivilege):
            reader.execute("SELECT given_name FROM composer.patient_snapshot")
