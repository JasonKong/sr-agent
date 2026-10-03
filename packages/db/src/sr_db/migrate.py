"""A small forward-only migration runner for plain SQL files.

Files are named `NNNN_description.sql` and applied in version order, each in its own
transaction. Applied files are recorded with a checksum; editing a file after it has been
applied is an error, so every schema change is a new file.
"""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

# Arbitrary constant shared by every runner, so concurrent runs wait for each other.
_LOCK_KEY = 7_412_903_001


class MigrationError(Exception):
    """The migration files or the recorded history are inconsistent."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: bytes
    checksum: str


def discover(directory: Path) -> list[Migration]:
    """Read every `.sql` file in `directory`, validated and sorted by version."""
    migrations: dict[int, Migration] = {}
    for path in directory.glob("*.sql"):
        match = _NAME.match(path.name)
        if match is None:
            raise MigrationError(
                f"{path.name}: migration files must be named NNNN_lowercase_words.sql"
            )
        version = int(match.group(1))
        if version in migrations:
            raise MigrationError(
                f"version {match.group(1)} is used by both "
                f"{migrations[version].name} and {path.name}"
            )
        body = path.read_bytes()
        migrations[version] = Migration(
            version=version,
            name=path.name,
            sql=body,
            checksum=hashlib.sha256(body).hexdigest(),
        )
    return [migrations[v] for v in sorted(migrations)]


def plan(available: list[Migration], applied: Mapping[str, str]) -> list[Migration]:
    """Return the migrations still to apply, after checking the recorded history.

    `applied` maps each applied file name to the checksum recorded when it ran.
    """
    by_name = {m.name: m for m in available}
    for name, checksum in applied.items():
        migration = by_name.get(name)
        if migration is None:
            raise MigrationError(f"{name} was applied but is missing from disk")
        if migration.checksum != checksum:
            raise MigrationError(
                f"{name} has changed since it was applied; add a new migration instead"
            )

    latest_applied = max((by_name[n].version for n in applied), default=0)
    pending = [m for m in available if m.name not in applied]
    for migration in pending:
        if migration.version < latest_applied:
            raise MigrationError(
                f"{migration.name} is out of order: a later migration is already applied"
            )
    return pending


def _ensure_history_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            name       text PRIMARY KEY,
            checksum   text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def applied_migrations(conn: psycopg.Connection) -> dict[str, str]:
    """Applied file names mapped to their recorded checksums, in name order."""
    _ensure_history_table(conn)
    rows = conn.execute(
        "SELECT name, checksum FROM public.schema_migrations ORDER BY name"
    ).fetchall()
    return dict(rows)


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Apply pending migrations and return them.

    `conn` must be in autocommit mode, so each migration gets a real transaction.
    """
    if not conn.autocommit:
        raise ValueError("migrate() needs an autocommit connection")

    conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
    try:
        pending = plan(discover(directory), applied_migrations(conn))
        for migration in pending:
            with conn.transaction():
                conn.execute(migration.sql)
                conn.execute(
                    "INSERT INTO public.schema_migrations (name, checksum) VALUES (%s, %s)",
                    (migration.name, migration.checksum),
                )
        return pending
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
