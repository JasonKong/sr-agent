"""Retrieval evaluation: 40 labelled queries, recall@5 and MRR (DESIGN.md 7.3, T4.3).

Each query is labelled with the root-cause code whose knowledge should come back. A hit is
relevant when its chunk has that code, from a ticket or a runbook. With about 30 tickets
per code, "found all relevant chunks" is not the question; "found the right kind of
knowledge" is. So:

- **recall@5**: the share of queries with at least one relevant hit in the top five;
- **MRR**: the mean of 1 / rank of the first relevant hit (0 when none is in the top five);
- **runbook@5**: stricter, the share of queries whose code's own runbook is in the top five.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from statistics import fmean

from pydantic import BaseModel

from sr_db.domain import FailureCode, KnowledgeSource
from sr_evals.dataset import DATASET_DIR
from sr_mcp.adapters.base import KnowledgeHit

RETRIEVAL_V1 = DATASET_DIR / "retrieval_v1.json"
K = 5


class RetrievalQuery(BaseModel):
    query_id: str
    code: FailureCode
    text: str


class QueryResult(BaseModel):
    query_id: str
    code: FailureCode
    first_relevant_rank: int | None
    runbook_rank: int | None
    top: list[str]  # chunk ids
    top_codes: list[str | None]


class RetrievalScore(BaseModel):
    queries: int
    recall_at_5: float
    mrr: float
    runbook_at_5: float
    per_code: dict[str, float]  # recall@5 per code
    results: list[QueryResult]


class _File(BaseModel):
    version: str
    note: str
    queries: list[RetrievalQuery]


def load_queries(path: Path = RETRIEVAL_V1) -> list[RetrievalQuery]:
    return _File.model_validate_json(path.read_text()).queries


def _rank(hits: Sequence[KnowledgeHit], code: str, *, runbook_only: bool = False) -> int | None:
    """The rank of the first hit of `code` in the top five, or None."""
    for n, hit in enumerate(hits[:K], start=1):
        if hit.error_code == code and (
            not runbook_only or hit.source_type is KnowledgeSource.RUNBOOK
        ):
            return n
    return None


def score_retrieval(
    queries: Sequence[RetrievalQuery], search: Callable[[RetrievalQuery], list[KnowledgeHit]]
) -> RetrievalScore:
    results = []
    for query in queries:
        hits = search(query)
        code = query.code.value
        results.append(
            QueryResult(
                query_id=query.query_id,
                code=query.code,
                first_relevant_rank=_rank(hits, code),
                runbook_rank=_rank(hits, code, runbook_only=True),
                top=[h.chunk_id for h in hits[:K]],
                top_codes=[h.error_code for h in hits[:K]],
            )
        )
    by_code: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_code[r.code.value].append(r.first_relevant_rank is not None)
    return RetrievalScore(
        queries=len(results),
        recall_at_5=fmean(r.first_relevant_rank is not None for r in results),
        mrr=fmean(1 / r.first_relevant_rank if r.first_relevant_rank else 0.0 for r in results),
        runbook_at_5=fmean(r.runbook_rank is not None for r in results),
        per_code={c: fmean(found) for c, found in sorted(by_code.items())},
        results=results,
    )
