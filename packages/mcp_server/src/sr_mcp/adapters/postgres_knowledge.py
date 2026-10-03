"""KnowledgeSearch over Postgres: runbooks now, ticket and runbook search in T4.2."""

from __future__ import annotations

from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from sr_mcp.adapters.base import KnowledgeHit, RunbookPage, RunbookSummary


class PostgresKnowledge:
    def __init__(self, url: str, *, max_connections: int = 2) -> None:
        self._pool: ConnectionPool[Connection[DictRow]] = ConnectionPool(
            url,
            connection_class=Connection[DictRow],
            min_size=1,
            max_size=max_connections,
            kwargs={"autocommit": True, "options": "-c timezone=UTC", "row_factory": dict_row},
            open=True,
        )

    def close(self) -> None:
        self._pool.close()

    def search(self, query: str, *, error_code: str | None, top_k: int) -> list[KnowledgeHit]:
        return []  # retrieval arrives in T4.2

    def list_runbooks(self) -> list[RunbookSummary]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT slug, error_code, title FROM knowledge.runbook"
                " ORDER BY error_code IS NULL, error_code, slug"
            ).fetchall()
        return [RunbookSummary(**row) for row in rows]

    def runbook(self, key: str) -> RunbookPage | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT slug, error_code, title, body_markdown, updated_at FROM knowledge.runbook"
                " WHERE error_code = %s OR slug = %s",
                (key, key),
            ).fetchone()
        return RunbookPage(**row) if row else None
