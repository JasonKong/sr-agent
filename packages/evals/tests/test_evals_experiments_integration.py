"""Retrieval experiments over a scratch database: one row per configuration."""

import pytest

from sr_evals.experiments import Config, render_experiments, run_experiments
from sr_evals.retrieval import RETRIEVAL_V1, load_queries
from sr_simulator.config import SimConfig

pytestmark = pytest.mark.integration


def test_every_configuration_is_measured_and_reported() -> None:
    world = SimConfig(seed=5, practices=4, clinics=6, referrals=150, tickets=80)
    configs = [Config(embedder="hash", chunk_chars=2000), Config(embedder="hash", chunk_chars=300)]
    rows = run_experiments(load_queries(RETRIEVAL_V1)[:10], configs, world=world)

    assert len(rows) == 2 * 3 * 2  # configs x modes x with and without the code filter
    assert {(r.chunk_chars, r.mode, r.code_filter) for r in rows} == {
        (size, mode, flt)
        for size in (2000, 300)
        for mode in ("hybrid", "vector", "keyword")
        for flt in (False, True)
    }
    assert all(0 <= r.score.recall_at_5 <= 1 for r in rows)
    # The filter is the labelled code, so vector and hybrid search can only find that code.
    assert all(r.score.recall_at_5 == 1.0 for r in rows if r.code_filter and r.mode != "keyword")
    assert max(r.chunks for r in rows if r.chunk_chars == 300) > max(
        r.chunks for r in rows if r.chunk_chars == 2000
    )
    table = render_experiments(rows)
    assert table.startswith("| Embedder | Chunk size |")
    assert table.count("\n") == len(rows) + 2
