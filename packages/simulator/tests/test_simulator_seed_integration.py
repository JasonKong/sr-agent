"""Integration tests: writing a generated world into Postgres, and the `sim seed` command."""

import json
import time
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from sr_simulator.cli import main
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.write import write_world

pytestmark = pytest.mark.integration

SMALL = SimConfig(seed=7, practices=6, clinics=8, referrals=300)


def _database_fingerprint(conn: psycopg.Connection) -> str:
    """An md5 over every row of every simulator table, in id order."""
    tables = conn.execute(
        """
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_schema IN ('composer', 'hub') ORDER BY 1, 2
        """
    ).fetchall()
    parts = []
    for schema, table in tables:
        row = conn.execute(
            sql.SQL(
                "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t.id), '')) FROM {} t"
            ).format(sql.Identifier(schema, table))
        ).fetchone()
        assert row is not None
        parts.append(f"{schema}.{table}:{row[0]}")
    return "\n".join(parts)


def test_writes_every_row(migrated: psycopg.Connection) -> None:
    world = generate(SMALL)

    counts = write_world(migrated, world)

    for table, rows in world.tables():
        row = migrated.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(*table.split(".")))
        ).fetchone()
        assert row == (len(rows),), table
        assert counts[table] == len(rows)


def test_reseeding_replaces_data_and_is_byte_identical(migrated: psycopg.Connection) -> None:
    write_world(migrated, generate(SMALL))
    first = _database_fingerprint(migrated)

    write_world(migrated, generate(SMALL))

    assert _database_fingerprint(migrated) == first


def test_a_different_seed_changes_the_database(migrated: psycopg.Connection) -> None:
    write_world(migrated, generate(SMALL))
    first = _database_fingerprint(migrated)

    write_world(migrated, generate(SimConfig(seed=8, practices=6, clinics=8, referrals=300)))

    assert _database_fingerprint(migrated) != first


def test_default_size_seeds_in_a_few_seconds(migrated: psycopg.Connection) -> None:
    started = time.perf_counter()

    write_world(migrated, generate(SimConfig()))

    assert time.perf_counter() - started < 15


def test_cli_seed(
    migrated: psycopg.Connection,
    migrated_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated_url)
    labels = tmp_path / "labels.json"

    exit_code = main(
        [
            *["seed", "--seed", "3", "--practices", "3", "--clinics", "4", "--referrals", "60"],
            *["--failure-rate", "0.2", "--labels", str(labels), "--logs", str(tmp_path / "logs")],
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "composer.referral" in out
    assert "F00=" in out
    row = migrated.execute("SELECT count(*) FROM composer.referral").fetchone()
    assert row == (60,)
    written = json.loads(labels.read_text())
    assert written["config"]["seed"] == 3
    assert 0 < len(written["labels"]) <= 12
    assert all(v.startswith("F") for v in written["labels"].values())
    assert written["config"]["referrals"] == 60
    assert len(list((tmp_path / "logs").glob("*.jsonl"))) == 4


def test_labels_never_reach_the_database(migrated: psycopg.Connection) -> None:
    write_world(migrated, generate(SMALL))
    leaked = migrated.execute(
        """
        SELECT count(*) FROM information_schema.columns
        WHERE table_schema IN ('composer', 'hub') AND column_name ILIKE '%label%'
        """
    ).fetchone()
    assert leaked == (0,)


def test_cli_rejects_an_anchor_time_without_timezone(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["seed", "--anchor-time", "2026-10-01T09:00:00"])
    assert "timezone" in capsys.readouterr().err
