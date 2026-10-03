"""Every piece of expected evidence can be reached through the tools.

For each failure code and distractor kind, the evidence a correct diagnosis should cite
(from the simulator's specs) must appear in the output of at least one tool, called the way
a model would: the referral, its timeline, deliveries, recipient, return path and logs.
If a record cannot be reached, no prompt can make the model cite it.
"""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from mcp import Client
from mcp.server import MCPServer

from sr_db.domain import FailureCode
from sr_mcp.adapters.jsonl_logs import JsonlLogSource
from sr_mcp.adapters.postgres import PostgresReferralStore
from sr_mcp.server import create_server
from sr_mcp.testing import fake_deps
from sr_simulator.case_rows import expected_evidence
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.logs import write_logs
from sr_simulator.rows import World
from sr_simulator.write import write_world

pytestmark = pytest.mark.integration

WORLD = SimConfig(
    seed=23, practices=8, clinics=14, referrals=700, failure_rate=0.2, distractor_rate=0.12
)
# Ticket evidence becomes reachable with search_knowledge (T4.2).
NOT_YET_REACHABLE = {"knowledge.support_ticket"}


@pytest.fixture(scope="module")
def world() -> World:
    return generate(WORLD)


@pytest.fixture
def server(
    migrated: psycopg.Connection, reader_url: str, world: World, tmp_path: Path
) -> Iterator[MCPServer]:
    write_world(migrated, world)
    write_logs(world, tmp_path)
    store = PostgresReferralStore(reader_url, mask_salt="reachable")
    yield create_server(fake_deps(store=store, logs=JsonlLogSource(tmp_path)))
    store.close()


def _everything_about(server: MCPServer, referral_id: str) -> str:
    """Call every evidence tool for the referral, as a thorough model would."""

    async def run() -> str:
        outputs: list[Any] = []
        async with Client(server) as client:
            referral = await client.call_tool("get_referral", {"referral_id": referral_id})
            outputs.append(referral.structured_content)
            clinic = referral.structured_content["referral"]["recipient"]["clinic"]["id"]
            for tool, args in [
                ("get_referral_timeline", {"referral_id": referral_id}),
                ("get_deliveries", {"referral_id": referral_id}),
                ("get_return_path", {"referral_id": referral_id}),
                ("get_recipient", {"clinic_id": clinic}),
                ("search_logs", {"referral_id": referral_id, "limit": 200}),
            ]:
                result = await client.call_tool(tool, args)
                assert not result.is_error, (tool, referral_id)
                outputs.append(result.structured_content)
        return json.dumps(outputs)

    return asyncio.run(run())


def _cases(world: World) -> list[tuple[str, str | None]]:
    cases: list[tuple[str, str | None]] = []
    for code in FailureCode:
        if code is FailureCode.F00_NO_FAULT:
            ids = [r.id for r in world.referrals if r.id not in world.labels][::90]
        else:
            ids = sorted(r for r, c in world.labels.items() if c is code)
        cases += [(rid, None) for rid in ids[:3] if rid not in world.distractors]
    for kind in sorted(set(world.distractors.values())):
        cases += [(r, kind) for r, k in sorted(world.distractors.items()) if k == kind][:2]
    return cases


def test_every_expected_evidence_record_is_reachable(server: MCPServer, world: World) -> None:
    unreachable: list[str] = []
    for referral_id, kind in _cases(world):
        seen = _everything_about(server, referral_id)
        code = world.labels.get(referral_id, FailureCode.F00_NO_FAULT).value
        for evidence in expected_evidence(world, referral_id, kind):
            if evidence.table in NOT_YET_REACHABLE:
                continue
            if evidence.record_id not in seen:
                unreachable.append(
                    f"{code}/{kind} {referral_id}: {evidence.table} {evidence.record_id}"
                )
    assert unreachable == []
