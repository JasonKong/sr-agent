"""Retrieval metrics: recall@5, MRR and runbook@5, on a fake search."""

from collections import Counter

import pytest

from sr_db.domain import FailureCode, KnowledgeSource
from sr_evals.retrieval import RETRIEVAL_V1, RetrievalQuery, load_queries, score_retrieval
from sr_mcp.adapters.base import KnowledgeHit


def hit(
    code: str | None, source: KnowledgeSource = KnowledgeSource.TICKET, n: int = 1
) -> KnowledgeHit:
    prefix = "tkt" if source is KnowledgeSource.TICKET else "rb"
    return KnowledgeHit(
        chunk_id=f"{prefix}_{n}#0",
        source_type=source,
        source_id=f"{prefix}_{n}",
        title="t",
        text="x",
        error_code=code,
        score=0.1,
    )


F07 = FailureCode.F07_CLINIC_PMS_AUTH_FAILED
F08 = FailureCode.F08_CLINIC_PMS_TIMEOUT
QUERIES = [
    RetrievalQuery(query_id="a", code=F07, text="token"),
    RetrievalQuery(query_id="b", code=F08, text="timeout"),
    RetrievalQuery(query_id="c", code=F07, text="auth"),
]
RESULTS = {
    "a": [hit(F07.value), hit(F08.value, n=2), hit(F07.value, KnowledgeSource.RUNBOOK, n=3)],
    "b": [hit(F07.value), hit(None, n=2), hit(F08.value, n=3)],
    "c": [hit(F08.value), hit(None), hit(None), hit(None), hit(None), hit(F07.value)],
}


def test_recall_mrr_and_runbook_at_five() -> None:
    score = score_retrieval(QUERIES, lambda q: RESULTS[q.query_id])

    assert score.queries == 3
    assert score.recall_at_5 == pytest.approx(2 / 3)  # "c" finds its code only at rank 6
    assert score.mrr == pytest.approx((1 + 1 / 3 + 0) / 3)
    assert score.runbook_at_5 == pytest.approx(1 / 3)
    assert score.per_code == {F07.value: 0.5, F08.value: 1.0}
    assert [r.first_relevant_rank for r in score.results] == [1, 3, None]


def test_the_labelled_queries_cover_every_code() -> None:
    queries = load_queries(RETRIEVAL_V1)
    counts = Counter(q.code for q in queries)

    assert len(queries) == 40
    assert set(counts) == set(FailureCode)
    assert min(counts.values()) >= 3
    assert len({q.text for q in queries}) == 40
