"""Pytest fixtures for integration tests in any package, loaded by the root conftest.py.

Each test gets its own throwaway database, so tests never touch the development data.
"""

from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from sr_db.migrate import MIGRATIONS_DIR, migrate
from sr_db.scratch import create_database, drop_database
from sr_db.scratch import database_url as url_for
from sr_db.settings import DatabaseSettings

_create = create_database
_drop = drop_database
_url = url_for


@pytest.fixture
def database_url() -> Iterator[str]:
    """An empty database for one test, dropped afterwards."""
    admin_url = DatabaseSettings().url
    name = _create(admin_url)
    try:
        yield _url(admin_url, name)
    finally:
        _drop(admin_url, name)


@pytest.fixture
def conn(database_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url, autocommit=True) as connection:
        yield connection


@pytest.fixture(scope="session")
def migrated_template() -> Iterator[str]:
    """A database migrated once per test session, used as a template for fast copies."""
    admin_url = DatabaseSettings().url
    name = _create(admin_url)
    try:
        with psycopg.connect(_url(admin_url, name), autocommit=True) as connection:
            migrate(connection, MIGRATIONS_DIR)
        yield name
    finally:
        _drop(admin_url, name)


@pytest.fixture
def migrated_url(migrated_template: str) -> Iterator[str]:
    """A fully migrated database for one test, copied from the session template."""
    admin_url = DatabaseSettings().url
    name = _create(admin_url, template=migrated_template)
    try:
        yield _url(admin_url, name)
    finally:
        _drop(admin_url, name)


@pytest.fixture
def migrated(migrated_url: str) -> Iterator[psycopg.Connection]:
    """A connection to a throwaway database with every shipped migration applied."""
    with psycopg.connect(migrated_url, autocommit=True) as connection:
        yield connection


READER_ROLE = "sr_test_reader"
READER_PASSWORD = "test-reader-password"


@pytest.fixture(scope="session")
def _reader_role(migrated_template: str) -> str:
    """A login role in sr_reader, created once (roles are shared by every database)."""
    admin_url = DatabaseSettings().url
    with psycopg.connect(admin_url, autocommit=True) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (READER_ROLE,)
        ).fetchone()
        verb = "ALTER" if exists else "CREATE"
        admin.execute(
            sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(
                sql.SQL(verb), sql.Identifier(READER_ROLE), sql.Literal(READER_PASSWORD)
            )
        )
        admin.execute(sql.SQL("GRANT sr_reader TO {}").format(sql.Identifier(READER_ROLE)))
    return READER_ROLE


@pytest.fixture
def reader_url(migrated_url: str, _reader_role: str) -> str:
    """The same throwaway database as `migrated`, logged in as a read-only role."""
    host_and_db = migrated_url.split("@", 1)[1]
    return f"postgresql://{_reader_role}:{READER_PASSWORD}@{host_and_db}"
