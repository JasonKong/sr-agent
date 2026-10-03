"""Integration tests against the docker-compose Postgres. Run with `make test-integration`.

Each test gets its own throwaway database, so tests never touch the development data.
"""

import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import pytest
from psycopg import sql

from sr_db.cli import main
from sr_db.migrate import MIGRATIONS_DIR, MigrationError, applied_migrations, migrate
from sr_db.settings import DatabaseSettings

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> Iterator[str]:
    """Create an empty database for one test and drop it afterwards."""
    admin_url = DatabaseSettings().url
    name = f"sr_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield urlsplit(admin_url)._replace(path=f"/{name}").geturl()
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def conn(database_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url, autocommit=True) as connection:
        yield connection


def test_applies_shipped_migrations_and_enables_pgvector(conn: psycopg.Connection) -> None:
    applied = migrate(conn, MIGRATIONS_DIR)

    assert applied[0].name == "0001_extensions.sql"
    assert "0001_extensions.sql" in applied_migrations(conn)
    row = conn.execute("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector").fetchone()
    assert row is not None
    assert row[0] == pytest.approx(1.0)


def test_second_run_applies_nothing(conn: psycopg.Connection) -> None:
    migrate(conn, MIGRATIONS_DIR)

    assert migrate(conn, MIGRATIONS_DIR) == []


def test_refuses_to_run_when_an_applied_file_changed(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    shutil.copytree(MIGRATIONS_DIR, tmp_path, dirs_exist_ok=True)
    migrate(conn, tmp_path)
    with (tmp_path / "0001_extensions.sql").open("a") as f:
        f.write("\n-- edited after apply\n")

    with pytest.raises(MigrationError, match="changed"):
        migrate(conn, tmp_path)


def test_failed_migration_is_rolled_back_and_not_recorded(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    (tmp_path / "0001_ok.sql").write_text("CREATE TABLE kept (id int);")
    (tmp_path / "0002_broken.sql").write_text(
        "CREATE TABLE half_done (id int);\nSELECT * FROM table_that_does_not_exist;"
    )

    with pytest.raises(psycopg.errors.UndefinedTable):
        migrate(conn, tmp_path)

    assert list(applied_migrations(conn)) == ["0001_ok.sql"]
    tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables").fetchall()}
    assert "kept" in tables
    assert "half_done" not in tables


def test_cli_migrate_then_status(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert main(["migrate"]) == 0
    assert "applied 0001_extensions.sql" in capsys.readouterr().out

    assert main(["status"]) == 0
    assert "[x] 0001_extensions.sql" in capsys.readouterr().out
