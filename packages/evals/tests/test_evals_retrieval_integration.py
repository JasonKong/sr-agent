"""Recall@5 of at least 0.8 on the 40 labelled queries, with the real local embedder."""

from collections.abc import Iterator

import psycopg
import pytest

from sr_evals.retrieval import RETRIEVAL_V1, load_queries, score_retrieval
from sr_mcp.adapters.postgres_knowledge import PostgresKnowledge
from sr_mcp.knowledge.embedder import make_embedder
from sr_mcp.knowledge.ingest import ingest
from sr_mcp.paths import project_root
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.write import write_world

pytestmark = pytest.mark.integration


@pytest.fixture
def knowledge(migrated: psycopg.Connection, reader_url: str) -> Iterator[PostgresKnowledge]:
    write_world(migrated, generate(SimConfig()))
    embedder = make_embedder("bge-small", cache_dir=project_root() / "var" / "models")
    ingest(migrated, embedder)
    search = PostgresKnowledge(reader_url, embedder=embedder)
    yield search
    search.close()


def test_hybrid_search_finds_the_right_knowledge(knowledge: PostgresKnowledge) -> None:
    queries = load_queries(RETRIEVAL_V1)
    score = score_retrieval(queries, lambda q: knowledge.search(q.text, error_code=None, top_k=5))
    misses = [r.query_id for r in score.results if r.first_relevant_rank is None]

    print(
        f"recall@5 {score.recall_at_5:.3f}, MRR {score.mrr:.3f}, runbook@5 {score.runbook_at_5:.3f}"
    )
    assert score.recall_at_5 >= 0.8, misses
    assert score.mrr >= 0.75  # measured 0.875 (recall@5 0.900, runbook@5 0.725)
