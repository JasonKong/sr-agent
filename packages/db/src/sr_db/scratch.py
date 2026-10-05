"""Throwaway databases, for tests and experiments that must never touch development data."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

from sr_db.migrate import MIGRATIONS_DIR, migrate


def create_database(admin_url: str, template: str | None = None, *, prefix: str = "sr_test") -> str:
    name = f"{prefix}_{uuid.uuid4().hex[:12]}"
    statement = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
    if template is not None:
        statement = sql.SQL("{} TEMPLATE {}").format(statement, sql.Identifier(template))
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(statement)
    return name


def drop_database(admin_url: str, name: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


def database_url(admin_url: str, name: str) -> str:
    return urlsplit(admin_url)._replace(path=f"/{name}").geturl()


@contextmanager
def scratch_database(admin_url: str, *, prefix: str = "sr_scratch") -> Iterator[str]:
    """The URL of a new, fully migrated database, dropped afterwards."""
    name = create_database(admin_url, prefix=prefix)
    try:
        url = database_url(admin_url, name)
        with psycopg.connect(url, autocommit=True) as conn:
            migrate(conn, MIGRATIONS_DIR)
        yield url
    finally:
        drop_database(admin_url, name)
