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


def _create(admin_url: str, template: str | None = None) -> str:
    name = f"sr_test_{uuid.uuid4().hex[:12]}"
    statement = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
    if template is not None:
        statement = sql.SQL("{} TEMPLATE {}").format(statement, sql.Identifier(template))
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(statement)
    return name


def _drop(admin_url: str, name: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


def _url(admin_url: str, name: str) -> str:
    return urlsplit(admin_url)._replace(path=f"/{name}").geturl()


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
