"""Hybrid retrieval over `knowledge.knowledge_chunk` (DESIGN.md section 7.3).

Two ranked lists are fused with reciprocal rank fusion (RRF): vector similarity of the
query embedding, and full-text rank of the query words. RRF uses ranks, not scores, so
the two very different score scales never need calibrating. Each chunk scores
sum(1 / (k + rank)) over the lists it appears in; k = 60 is the usual constant.

`mode` selects vector only, keyword only or hybrid, for the retrieval experiments (T4.4).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

SearchMode = Literal["hybrid", "vector", "keyword"]
CANDIDATES = 20  # how deep each list goes before fusion
RRF_K = 60

_WORD = re.compile(r"[a-z0-9]+")


def fuse(rankings: Sequence[Sequence[str]], *, k: int = RRF_K) -> list[tuple[str, float]]:
    """Chunk ids by fused score, best first; ties keep first-seen order."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: -item[1])


def keyword_query(text: str) -> str:
    """A `to_tsquery` string matching any of the words. Only [a-z0-9] survive, so the
    query can never inject tsquery operators."""
    return " | ".join(_WORD.findall(text.lower()))


VECTOR_SQL = """
SELECT id FROM knowledge.knowledge_chunk
WHERE embedder = %(embedder)s AND (%(code)s::text IS NULL OR error_code = %(code)s)
ORDER BY embedding <=> %(vector)s::vector
LIMIT %(limit)s
"""

KEYWORD_SQL = """
SELECT id FROM knowledge.knowledge_chunk, to_tsquery('english', %(query)s) AS q
WHERE tsv @@ q AND (%(code)s::text IS NULL OR error_code = %(code)s)
ORDER BY ts_rank_cd(tsv, q) DESC, id
LIMIT %(limit)s
"""

HITS_SQL = """
SELECT id AS chunk_id, source_type, source_id, title, text, error_code
FROM knowledge.knowledge_chunk WHERE id = ANY(%(ids)s)
"""
