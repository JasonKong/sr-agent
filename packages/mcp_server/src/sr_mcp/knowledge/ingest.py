"""Ingest tickets and runbooks into `knowledge.knowledge_chunk` (DESIGN.md section 7.3).

Idempotent: each chunk's hash covers its text, metadata and embedder, so a re-run embeds
only chunks that are new or changed, and deletes chunks whose source has gone (or which a
new chunk size no longer produces). Runs as the database owner (`DATABASE_URL`), never as
the MCP server's read-only role.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row

from sr_mcp.knowledge.chunking import Chunk, runbook_chunks, ticket_chunk
from sr_mcp.knowledge.embedder import Embedder


@dataclass(frozen=True)
class IngestCounts:
    inserted: int
    updated: int
    unchanged: int
    deleted: int


@dataclass(frozen=True)
class _Ticket:
    id: str
    referral_ref: str | None
    clinic_ref: str | None
    title: str
    description: str
    resolution: str | None
    root_cause_code: str | None
    created_at: datetime


@dataclass(frozen=True)
class _Runbook:
    id: str
    slug: str
    error_code: str | None
    title: str
    body_markdown: str


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


def all_chunks(conn: psycopg.Connection, *, max_chars: int) -> list[Chunk]:
    with conn.cursor(row_factory=dict_row) as cur:
        tickets = cur.execute(
            "SELECT id, referral_ref, clinic_ref, title, description, resolution,"
            " root_cause_code, created_at FROM knowledge.support_ticket ORDER BY id"
        ).fetchall()
        runbooks = cur.execute(
            "SELECT id, slug, error_code, title, body_markdown FROM knowledge.runbook ORDER BY id"
        ).fetchall()
    chunks = [ticket_chunk(_Ticket(**row)) for row in tickets]
    for row in runbooks:
        chunks += runbook_chunks(_Runbook(**row), max_chars=max_chars)
    return chunks


def ingest(
    conn: psycopg.Connection,
    embedder: Embedder,
    *,
    max_chars: int = 2000,
    now: datetime | None = None,
    batch_size: int = 64,
) -> IngestCounts:
    chunks = all_chunks(conn, max_chars=max_chars)
    existing: dict[str, str] = {
        row[0]: row[1]
        for row in conn.execute("SELECT id, content_hash FROM knowledge.knowledge_chunk")
    }
    todo = [c for c in chunks if existing.get(c.id) != c.content_hash(embedder.name)]
    vectors: list[list[float]] = []
    for start in range(0, len(todo), batch_size):
        batch = todo[start : start + batch_size]
        vectors += embedder.embed_documents([c.embedding_text() for c in batch])
    wanted = {c.id for c in chunks}
    gone = sorted(set(existing) - wanted)
    stamp = now or datetime.now(UTC)
    with conn.transaction():
        for chunk, vector in zip(todo, vectors, strict=True):
            conn.execute(
                """
                INSERT INTO knowledge.knowledge_chunk (
                    id, source_type, source_id, chunk_no, error_code, title, text, metadata,
                    content_hash, embedder, embedding, ingested_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
                ON CONFLICT (id) DO UPDATE SET
                    source_type = EXCLUDED.source_type, source_id = EXCLUDED.source_id,
                    chunk_no = EXCLUDED.chunk_no, error_code = EXCLUDED.error_code,
                    title = EXCLUDED.title, text = EXCLUDED.text, metadata = EXCLUDED.metadata,
                    content_hash = EXCLUDED.content_hash, embedder = EXCLUDED.embedder,
                    embedding = EXCLUDED.embedding, ingested_at = EXCLUDED.ingested_at
                """,
                (
                    chunk.id,
                    chunk.source_type.value,
                    chunk.source_id,
                    chunk.chunk_no,
                    chunk.error_code,
                    chunk.title,
                    chunk.text,
                    json.dumps(chunk.metadata),
                    chunk.content_hash(embedder.name),
                    embedder.name,
                    vector_literal(vector),
                    stamp,
                ),
            )
        if gone:
            conn.execute("DELETE FROM knowledge.knowledge_chunk WHERE id = ANY(%s)", (gone,))
    updated = sum(1 for c in todo if c.id in existing)
    return IngestCounts(
        inserted=len(todo) - updated,
        updated=updated,
        unchanged=len(chunks) - len(todo),
        deleted=len(gone),
    )
