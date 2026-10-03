"""Pytest fixtures for integration tests in any package, loaded by the root conftest.py.

Each test gets its own throwaway database, so tests never touch the development data.
"""

import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit

import psycopg
import pytest
from psycopg import sql

from sr_db.migrate import MIGRATIONS_DIR, migrate
from sr_db.settings import DatabaseSettings


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


@pytest.fixture
def migrated(conn: psycopg.Connection) -> psycopg.Connection:
    """A throwaway database with every shipped migration applied."""
    migrate(conn, MIGRATIONS_DIR)
    return conn
