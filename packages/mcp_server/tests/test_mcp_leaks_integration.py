"""Leak tests: no tool may ever return a patient's name, NHI or date of birth.

Every registered tool must have inputs in TOOL_INPUTS; a new tool without them fails
`test_every_tool_is_leak_tested`. Each tool is called on real seeded cases, through the
read-only database role, and its whole output is searched for every patient value.
"""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import psycopg
import pytest
from mcp import Client
from mcp.types import TextContent

from sr_mcp.adapters.postgres import PostgresReferralStore
from sr_mcp.server import create_server
from sr_mcp.testing import fake_deps
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.rows import World
from sr_simulator.write import write_world

pytestmark = pytest.mark.integration

WORLD = SimConfig(seed=11, practices=6, clinics=10, referrals=250)

# Tool name -> function building the tool's arguments for one referral of the seeded world.
TOOL_INPUTS: dict[str, Callable[[World, str], dict[str, Any]]] = {}


@pytest.fixture(scope="module")
def world() -> World:
    return generate(WORLD)


def _tool_names() -> list[str]:
    async def run() -> list[str]:
        async with Client(create_server(fake_deps())) as client:
            return [t.name for t in (await client.list_tools()).tools]

    return asyncio.run(run())


def test_every_tool_is_leak_tested() -> None:
    assert set(_tool_names()) == set(TOOL_INPUTS)


def test_patient_fields_through_the_reader_role_reveal_status_only(
    migrated: psycopg.Connection, reader_url: str, world: World
) -> None:
    write_world(migrated, world)
    store = PostgresReferralStore(reader_url, mask_salt="leak-test")
    try:
        for patient in world.patients[:50]:
            fields = store.patient_fields(patient.id)
            assert fields is not None
            dumped = fields.model_dump_json()
            full_name = f"{patient.given_name} {patient.family_name}"
            for value in (patient.synthetic_nhi, patient.date_of_birth, full_name):
                assert not value or value not in dumped
        assert store.patient_fields("pt_999999") is None
    finally:
        store.close()


@pytest.mark.parametrize("tool", sorted(TOOL_INPUTS))
def test_no_tool_leaks_patient_values(
    migrated: psycopg.Connection, reader_url: str, world: World, tool: str
) -> None:
    write_world(migrated, world)
    store = PostgresReferralStore(reader_url, mask_salt="leak-test")
    server = create_server(fake_deps(store=store))
    # Full names, NHIs and dates of birth (single names alone would match ordinary words).
    secrets = {
        v
        for p in world.patients
        for v in (p.synthetic_nhi, p.date_of_birth, f"{p.given_name} {p.family_name}")
        if v
    }
    referral_ids = [r.id for r in world.referrals[:: max(1, len(world.referrals) // 40)]]

    async def run() -> list[str]:
        outputs = []
        async with Client(server) as client:
            for referral_id in referral_ids:
                result = await client.call_tool(tool, TOOL_INPUTS[tool](world, referral_id))
                text = "".join(c.text for c in result.content if isinstance(c, TextContent))
                outputs.append(json.dumps(result.structured_content) + text)
        return outputs

    try:
        for output in asyncio.run(run()):
            leaked = [s for s in secrets if s in output]
            assert leaked == [], f"{tool} leaked patient values"
    finally:
        store.close()
