"""Retrieval experiments (DESIGN.md 7.3, T4.4): every configuration on a scratch database.

For each embedder and runbook chunk size, the default world is written to a new database
and ingested; then each search mode is scored on the labelled queries, with and without the
`error_code` filter. The filter uses each query's labelled code, so it shows the upper
bound: what filtering gives when the agent already suspects the right code.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg
from pydantic import BaseModel

from sr_db.scratch import scratch_database
from sr_db.settings import DatabaseSettings
from sr_evals.retrieval import RetrievalQuery, RetrievalScore, score_retrieval
from sr_mcp.adapters.base import KnowledgeHit
from sr_mcp.adapters.postgres_knowledge import PostgresKnowledge
from sr_mcp.knowledge.embedder import make_embedder
from sr_mcp.knowledge.ingest import ingest
from sr_mcp.knowledge.search import SearchMode
from sr_mcp.paths import project_root
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.write import write_world

MODES: tuple[SearchMode, ...] = ("hybrid", "vector", "keyword")


@dataclass(frozen=True)
class Config:
    embedder: str
    chunk_chars: int


class ExperimentRow(BaseModel):
    embedder: str
    chunk_chars: int
    chunks: int
    ingest_s: float
    mode: SearchMode
    code_filter: bool
    score: RetrievalScore


def _searcher(
    knowledge: PostgresKnowledge, mode: SearchMode, code_filter: bool
) -> Callable[[RetrievalQuery], list[KnowledgeHit]]:
    def search(query: RetrievalQuery) -> list[KnowledgeHit]:
        code = query.code.value if code_filter else None
        return knowledge.search(query.text, error_code=code, top_k=5, mode=mode)

    return search


def run_experiments(
    queries: Sequence[RetrievalQuery],
    configs: Sequence[Config],
    *,
    world: SimConfig | None = None,
    admin_url: str | None = None,
    model_dir: Path | None = None,
) -> list[ExperimentRow]:
    root = project_root()
    admin = admin_url or DatabaseSettings(_env_file=root / ".env").url
    models = model_dir or root / "var" / "models"
    rows_of_world = generate(world or SimConfig())
    rows: list[ExperimentRow] = []
    for config in configs:
        embedder = make_embedder(config.embedder, cache_dir=models)
        with scratch_database(admin) as url:
            with psycopg.connect(url, autocommit=True) as conn:
                write_world(conn, rows_of_world)
                started = time.perf_counter()
                ingest(conn, embedder, max_chars=config.chunk_chars)
                ingest_s = round(time.perf_counter() - started, 1)
                counted = conn.execute("SELECT count(*) FROM knowledge.knowledge_chunk").fetchone()
                chunks = int(counted[0]) if counted else 0
            knowledge = PostgresKnowledge(url, embedder=embedder)
            try:
                for mode in MODES:
                    for code_filter in (False, True):
                        score = score_retrieval(queries, _searcher(knowledge, mode, code_filter))
                        rows.append(
                            ExperimentRow(
                                embedder=embedder.name,
                                chunk_chars=config.chunk_chars,
                                chunks=chunks,
                                ingest_s=ingest_s,
                                mode=mode,
                                code_filter=code_filter,
                                score=score,
                            )
                        )
            finally:
                knowledge.close()
    return rows


def render_experiments(rows: Sequence[ExperimentRow]) -> str:
    lines = [
        "| Embedder | Chunk size | Chunks | Mode | Code filter | recall@5 | MRR | runbook@5 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {r.embedder} | {r.chunk_chars} | {r.chunks} | {r.mode} | "
        f"{'yes' if r.code_filter else 'no'} | {r.score.recall_at_5:.3f} | {r.score.mrr:.3f} | "
        f"{r.score.runbook_at_5:.3f} |"
        for r in rows
    ]
    return "\n".join(lines) + "\n"
