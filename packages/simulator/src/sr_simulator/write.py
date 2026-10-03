"""Write a generated World into the composer and hub schemas."""

from __future__ import annotations

import dataclasses
from enum import Enum
from typing import TYPE_CHECKING

import psycopg
from psycopg import sql

from sr_simulator.rows import World

if TYPE_CHECKING:
    from _typeshed import DataclassInstance


def _values(row: DataclassInstance, columns: list[str]) -> tuple[object, ...]:
    return tuple(
        value.value if isinstance(value, Enum) else value
        for value in (getattr(row, column) for column in columns)
    )


def write_world(conn: psycopg.Connection, world: World) -> dict[str, int]:
    """Replace all simulator data with `world` in one transaction; return rows per table."""
    tables = world.tables()
    counts: dict[str, int] = {}
    with conn.transaction():
        conn.execute(
            sql.SQL("TRUNCATE {}").format(
                sql.SQL(", ").join(sql.Identifier(*table.split(".")) for table, _ in tables)
            )
        )
        with conn.cursor() as cursor:
            for table, rows in tables:
                counts[table] = len(rows)
                if not rows:
                    continue
                columns = [f.name for f in dataclasses.fields(rows[0])]
                copy = sql.SQL("COPY {} ({}) FROM STDIN").format(
                    sql.Identifier(*table.split(".")),
                    sql.SQL(", ").join(map(sql.Identifier, columns)),
                )
                with cursor.copy(copy) as writer:
                    for row in rows:
                        writer.write_row(_values(row, columns))
    return counts
