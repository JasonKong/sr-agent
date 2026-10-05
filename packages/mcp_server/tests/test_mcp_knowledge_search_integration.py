"""Hybrid search over ingested chunks, through the read-only role."""

from collections.abc import Iterator

import psycopg
import pytest

from sr_mcp.adapters.postgres_knowledge import PostgresKnowledge
from sr_mcp.knowledge.embedder import HashEmbedder
from sr_mcp.knowledge.ingest import ingest
from sr_mcp.knowledge.search import SearchMode
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.rows import World
from sr_simulator.write import write_world

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def world() -> World:
    return generate(SimConfig(seed=13, practices=4, clinics=8, referrals=200, tickets=120))


@pytest.fixture
def knowledge(
    migrated: psycopg.Connection, reader_url: str, world: World
) -> Iterator[PostgresKnowledge]:
    write_world(migrated, world)
    ingest(migrated, HashEmbedder())
    search = PostgresKnowledge(reader_url, embedder=HashEmbedder())
    yield search
    search.close()


@pytest.mark.parametrize("mode", ["hybrid", "vector", "keyword"])
def test_a_tickets_own_words_find_it_in_every_mode(
    knowledge: PostgresKnowledge, world: World, mode: SearchMode
) -> None:
    found = 0
    for ticket in world.support_tickets[:20]:
        query = ticket.title + " " + ticket.description[:120]
        hits = knowledge.search(query, error_code=None, top_k=5, mode=mode)
        found += ticket.id in [h.source_id for h in hits]
    # Measured with the hash embedder: hybrid 19, vector 20, keyword 16 of 20.
    assert found >= 15, (mode, found)


def test_a_code_filter_returns_only_that_code(knowledge: PostgresKnowledge) -> None:
    hits = knowledge.search("token expired", error_code="F08_CLINIC_PMS_TIMEOUT", top_k=5)
    assert hits
    assert {h.error_code for h in hits} == {"F08_CLINIC_PMS_TIMEOUT"}


def test_runbooks_are_found_by_their_symptoms(knowledge: PostgresKnowledge) -> None:
    hits = knowledge.search("clinic has no active account routed manual", error_code=None, top_k=5)
    assert any(h.source_type == "RUNBOOK" and h.error_code == "F06_MANUAL_ROUTING" for h in hits)


def test_hits_are_ranked_best_first_and_capped(knowledge: PostgresKnowledge) -> None:
    hits = knowledge.search("PMS authorisation token", error_code=None, top_k=3)
    assert len(hits) == 3
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_no_words_and_no_embedder_finds_nothing(
    reader_url: str, knowledge: PostgresKnowledge
) -> None:
    keyword_only = PostgresKnowledge(reader_url)
    try:
        assert keyword_only.search("?!", error_code=None, top_k=5) == []
        assert keyword_only.search("manual routing", error_code=None, top_k=5)
    finally:
        keyword_only.close()


def test_a_query_embedded_by_another_model_is_not_compared(
    reader_url: str, knowledge: PostgresKnowledge
) -> None:
    class Other(HashEmbedder):
        def __init__(self) -> None:
            super().__init__()
            self.name = "other-384"

    other = PostgresKnowledge(reader_url, embedder=Other())
    try:
        assert other.search("zzz qqq", error_code=None, top_k=5, mode="vector") == []
    finally:
        other.close()
