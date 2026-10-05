"""KnowledgeSearch over Postgres: hybrid search over chunks, and runbooks (DESIGN.md 7.3)."""

from __future__ import annotations

from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from sr_mcp.adapters.base import KnowledgeHit, RunbookPage, RunbookSummary
from sr_mcp.knowledge.embedder import Embedder
from sr_mcp.knowledge.ingest import vector_literal
from sr_mcp.knowledge.search import (
    CANDIDATES,
    HITS_SQL,
    KEYWORD_SQL,
    VECTOR_SQL,
    SearchMode,
    fuse,
    keyword_query,
)


class PostgresKnowledge:
    def __init__(
        self, url: str, *, embedder: Embedder | None = None, max_connections: int = 2
    ) -> None:
        """Without an embedder, search is keyword only."""
        self._embedder = embedder
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

    def search(
        self,
        query: str,
        *,
        error_code: str | None,
        top_k: int,
        mode: SearchMode = "hybrid",
    ) -> list[KnowledgeHit]:
        rankings: list[list[str]] = []
        with self._pool.connection() as conn:
            if mode in ("hybrid", "vector") and self._embedder is not None:
                vector = vector_literal(self._embedder.embed_query(query))
                rows = conn.execute(
                    VECTOR_SQL,
                    {
                        "embedder": self._embedder.name,
                        "code": error_code,
                        "vector": vector,
                        "limit": CANDIDATES,
                    },
                ).fetchall()
                rankings.append([r["id"] for r in rows])
            words = keyword_query(query)
            if mode in ("hybrid", "keyword") and words:
                rows = conn.execute(
                    KEYWORD_SQL, {"query": words, "code": error_code, "limit": CANDIDATES}
                ).fetchall()
                rankings.append([r["id"] for r in rows])
            ranked = fuse(rankings)[:top_k]
            if not ranked:
                return []
            found = {
                r["chunk_id"]: r
                for r in conn.execute(HITS_SQL, {"ids": [c for c, _ in ranked]}).fetchall()
            }
        return [
            KnowledgeHit(**found[chunk_id], score=round(score, 5)) for chunk_id, score in ranked
        ]

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
