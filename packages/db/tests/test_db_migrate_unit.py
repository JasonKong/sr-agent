"""Unit tests for migration discovery and planning. No database needed."""

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from sr_db.migrate import MIGRATIONS_DIR, Migration, MigrationError, discover, plan
from sr_db.settings import DatabaseSettings


def _write(directory: Path, name: str, sql: str = "SELECT 1;") -> None:
    (directory / name).write_text(sql)


def _migration(version: int, name: str, sql: bytes = b"SELECT 1;") -> Migration:
    return Migration(version=version, name=name, sql=sql, checksum=hashlib.sha256(sql).hexdigest())


class TestDiscover:
    def test_sorts_by_version(self, tmp_path: Path) -> None:
        _write(tmp_path, "0002_second.sql")
        _write(tmp_path, "0010_tenth.sql")
        _write(tmp_path, "0001_first.sql")

        assert [m.name for m in discover(tmp_path)] == [
            "0001_first.sql",
            "0002_second.sql",
            "0010_tenth.sql",
        ]

    def test_checksum_is_sha256_of_file_bytes(self, tmp_path: Path) -> None:
        _write(tmp_path, "0001_first.sql", "CREATE TABLE t (id int);")

        (migration,) = discover(tmp_path)

        assert migration.version == 1
        assert migration.checksum == hashlib.sha256(b"CREATE TABLE t (id int);").hexdigest()

    def test_ignores_files_that_are_not_sql(self, tmp_path: Path) -> None:
        _write(tmp_path, "0001_first.sql")
        _write(tmp_path, "README.md", "notes")

        assert [m.name for m in discover(tmp_path)] == ["0001_first.sql"]

    @pytest.mark.parametrize("bad_name", ["1_first.sql", "0001-first.sql", "0001_First.sql"])
    def test_rejects_badly_named_sql_files(self, tmp_path: Path, bad_name: str) -> None:
        _write(tmp_path, bad_name)

        with pytest.raises(MigrationError, match=bad_name):
            discover(tmp_path)

    def test_rejects_duplicate_versions(self, tmp_path: Path) -> None:
        _write(tmp_path, "0001_first.sql")
        _write(tmp_path, "0001_also_first.sql")

        with pytest.raises(MigrationError, match="0001"):
            discover(tmp_path)

    def test_shipped_migrations_are_valid(self) -> None:
        names = [m.name for m in discover(MIGRATIONS_DIR)]

        assert names[0] == "0001_extensions.sql"


class TestPlan:
    def test_returns_only_pending_migrations_in_order(self) -> None:
        first, second, third = (
            _migration(1, "0001_a.sql"),
            _migration(2, "0002_b.sql"),
            _migration(3, "0003_c.sql"),
        )

        pending = plan([first, second, third], applied={first.name: first.checksum})

        assert pending == [second, third]

    def test_returns_nothing_when_all_applied(self) -> None:
        first = _migration(1, "0001_a.sql")

        assert plan([first], applied={first.name: first.checksum}) == []

    def test_rejects_a_migration_changed_after_it_was_applied(self) -> None:
        first = _migration(1, "0001_a.sql", b"SELECT 2;")

        with pytest.raises(MigrationError, match=r"0001_a\.sql.*changed"):
            plan([first], applied={first.name: "checksum-from-before"})

    def test_rejects_an_applied_migration_missing_from_disk(self) -> None:
        with pytest.raises(MigrationError, match=r"0001_a\.sql.*missing"):
            plan([], applied={"0001_a.sql": "abc"})

    def test_rejects_a_pending_migration_older_than_the_latest_applied(self) -> None:
        first, second = _migration(1, "0001_a.sql"), _migration(2, "0002_b.sql")

        with pytest.raises(MigrationError, match=r"0001_a\.sql.*out of order"):
            plan([first, second], applied={second.name: second.checksum})


class TestDatabaseSettings:
    def test_reads_database_url_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")

        settings = DatabaseSettings(_env_file=None)

        assert settings.url == "postgresql://u:p@localhost:5432/db"

    def test_requires_database_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with pytest.raises(ValidationError, match="database_url"):
            DatabaseSettings(_env_file=None)

    def test_rejects_non_postgres_urls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "mysql://u:p@localhost:3306/db")

        with pytest.raises(ValidationError):
            DatabaseSettings(_env_file=None)
