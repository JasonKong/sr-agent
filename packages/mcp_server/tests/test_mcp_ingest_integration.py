"""sr-ingest: every ticket and runbook chunked, masked, embedded once, kept in step."""

import re
from datetime import UTC, datetime

import psycopg
import pytest

from sr_mcp.knowledge.embedder import HashEmbedder
from sr_mcp.knowledge.ingest import IngestCounts, ingest
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.rows import World
from sr_simulator.write import write_world

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 1, tzinfo=UTC)


class Counting(HashEmbedder):
    def __init__(self, name: str = "hash-384") -> None:
        super().__init__()
        self.name = name
        self.texts = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.texts += len(texts)
        return super().embed_documents(texts)


@pytest.fixture
def world() -> World:
    return generate(SimConfig(seed=11, practices=4, clinics=6, referrals=200, tickets=60))


def chunks(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT count(*) FROM knowledge.knowledge_chunk").fetchone()
    assert row is not None
    return int(row[0])


def test_a_first_ingest_embeds_everything_and_a_second_nothing(
    migrated: psycopg.Connection, world: World
) -> None:
    write_world(migrated, world)
    embedder = Counting()
    first = ingest(migrated, embedder, now=NOW)

    assert first.inserted == chunks(migrated) == embedder.texts
    assert first.inserted >= len(world.support_tickets) + len(world.runbooks)
    second = ingest(migrated, embedder, now=NOW)
    assert second == IngestCounts(inserted=0, updated=0, unchanged=first.inserted, deleted=0)
    assert embedder.texts == first.inserted  # nothing embedded twice


def test_changes_are_picked_up_and_removed_sources_removed(
    migrated: psycopg.Connection, world: World
) -> None:
    write_world(migrated, world)
    ingest(migrated, Counting(), now=NOW)
    ticket, gone = world.support_tickets[0].id, world.support_tickets[1].id
    migrated.execute(
        "UPDATE knowledge.support_ticket SET description = description || ' Updated.'"
        " WHERE id = %s",
        (ticket,),
    )
    migrated.execute("DELETE FROM knowledge.support_ticket WHERE id = %s", (gone,))

    counts = ingest(migrated, Counting(), now=NOW)

    assert (counts.updated, counts.deleted, counts.inserted) == (1, 1, 0)
    row = migrated.execute(
        "SELECT text FROM knowledge.knowledge_chunk WHERE source_id = %s", (ticket,)
    ).fetchone()
    assert row is not None
    assert row[0].endswith("Updated.") or "Updated." in row[0]


def test_another_embedder_or_chunk_size_re_embeds(
    migrated: psycopg.Connection, world: World
) -> None:
    write_world(migrated, world)
    first = ingest(migrated, Counting("hash-384"), now=NOW)
    switched = ingest(migrated, Counting("other-384"), now=NOW)
    assert switched.updated == first.inserted

    smaller = ingest(migrated, Counting("other-384"), max_chars=300, now=NOW)
    assert smaller.inserted > 0  # runbooks split into more chunks
    assert chunks(migrated) == first.inserted + smaller.inserted - smaller.deleted


def test_no_patient_identifier_reaches_the_knowledge_base(
    migrated: psycopg.Connection, world: World
) -> None:
    write_world(migrated, world)
    ingest(migrated, Counting(), now=NOW)
    text = " ".join(
        r[0] for r in migrated.execute("SELECT title || ' ' || text FROM knowledge.knowledge_chunk")
    )
    raw = " ".join(t.description for t in world.support_tickets)

    assert re.search(r"\bSIM\d{4}\b", raw)  # the tickets do carry identifiers
    assert not re.search(r"\bSIM\d{4}\b", text)
    for patient in world.patients:
        if patient.date_of_birth and len(patient.date_of_birth) >= 8:
            assert patient.date_of_birth not in text


def test_chunks_are_indexed_for_vector_and_full_text_search(migrated: psycopg.Connection) -> None:
    indexes = {
        r[0]
        for r in migrated.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'knowledge_chunk'"
        )
    }
    assert any("hnsw" in i and "vector_cosine_ops" in i for i in indexes)
    assert any("gin" in i and "tsv" in i for i in indexes)
