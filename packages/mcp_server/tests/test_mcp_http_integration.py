"""HTTP transport against the seeded database, through the read-only role."""

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx2
import psycopg
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from sr_mcp.adapters.postgres import PostgresReferralStore
from sr_mcp.http import build_app
from sr_mcp.server import create_server
from sr_mcp.testing import fake_deps
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.rows import World
from sr_simulator.write import write_world

pytestmark = pytest.mark.integration

TOKEN = "integration-" + "k" * 32


@pytest.fixture
def world() -> World:
    return generate(SimConfig(seed=31, practices=4, clinics=6, referrals=150))


@pytest.fixture
def served(
    migrated: psycopg.Connection, reader_url: str, world: World
) -> Iterator[tuple[str, PostgresReferralStore]]:
    write_world(migrated, world)
    store = PostgresReferralStore(reader_url, mask_salt="http")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = build_app(create_server(fake_deps(store=store)), TOKEN, host="127.0.0.1")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", store
    server.should_exit = True
    thread.join(timeout=5)
    store.close()


def test_http_answers_match_the_store(
    served: tuple[str, PostgresReferralStore], world: World
) -> None:
    base_url, store = served
    referral = next(r for r in world.referrals if r.hub_referral_id)

    async def run() -> Any:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        url = f"{base_url}/mcp"
        async with (
            httpx2.AsyncClient(headers=headers) as http,
            Client(streamable_http_client(url, http_client=http)) as client,
        ):
            result = await client.call_tool("get_referral", {"referral_id": referral.id})
            return result.structured_content

    data = asyncio.run(run())
    expected = store.get_referral(referral.id)
    assert expected is not None
    assert data["referral"] == expected.model_dump(mode="json")
