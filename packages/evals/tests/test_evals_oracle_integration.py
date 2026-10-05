"""An agent that knows the answers scores 100% through the real tools and database.

The oracle looks with the same tools a careful agent would use, then cites the expected
evidence. If it scores below 100%, the dataset, the tools, the answer checks and the
scorers disagree somewhere: a bug in the pipeline, not in a model.
"""

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
import uvicorn

from sr_agent.toolbox import connect_http
from sr_evals.dataset import DATASET_V1, Dataset, load_dataset, world_for
from sr_evals.oracle import OracleModel
from sr_evals.report import summarise
from sr_evals.runner import CaseResult, run_cases
from sr_evals.scorers import WorldIndex, score_case
from sr_mcp.adapters.jsonl_logs import JsonlLogSource
from sr_mcp.adapters.postgres import PostgresReferralStore
from sr_mcp.http import build_app
from sr_mcp.server import create_server
from sr_mcp.testing import fake_deps
from sr_simulator.logs import write_logs
from sr_simulator.rows import World
from sr_simulator.write import write_world

pytestmark = pytest.mark.integration

TOKEN = "oracle-" + "k" * 40


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return load_dataset(DATASET_V1)


@pytest.fixture(scope="module")
def world(dataset: Dataset) -> World:
    return world_for(dataset)


@pytest.fixture
def mcp_url(
    migrated: psycopg.Connection, reader_url: str, world: World, tmp_path: Path
) -> Iterator[str]:
    write_world(migrated, world)
    write_logs(world, tmp_path)
    store = PostgresReferralStore(reader_url, mask_salt="oracle")
    deps = fake_deps(store=store, logs=JsonlLogSource(tmp_path), max_result_chars=8000)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = build_app(create_server(deps), TOKEN, host="127.0.0.1")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)
    store.close()


def test_the_oracle_scores_full_marks_on_every_case(
    mcp_url: str, dataset: Dataset, world: World
) -> None:
    clinic_of = {r.id: r.recipient_clinic_ref for r in world.referrals}

    async def run() -> list[CaseResult]:
        async with connect_http(mcp_url, TOKEN) as toolbox:
            return await run_cases(
                dataset.cases,
                model_for=lambda c: OracleModel(c, clinic_of[c.composer_id]),
                toolbox=toolbox,
                concurrency=8,
            )

    results = asyncio.run(run())
    index = WorldIndex.of(world)
    scores = [score_case(r.case, r.run, index, error=r.error) for r in results]
    failing = {
        s.case_id: (s.outcome, s.key_evidence_recall, s.evidence_resolves)
        for s in scores
        if not (s.root_cause_correct and s.evidence_resolves and s.key_evidence_recall == 1.0)
    }
    dropped = {
        r.case.case_id: r.run.dropped_evidence for r in results if r.run and r.run.dropped_evidence
    }

    assert failing == {}, dropped
    metrics = summarise(scores, model="oracle")
    assert metrics.accuracy == 1.0
    assert metrics.no_patient_identifiers == 1.0
    assert metrics.escalation_correct == 1.0
    assert all(s.tool_calls == 6 for s in scores)
