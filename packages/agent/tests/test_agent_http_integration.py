"""The loop over HTTP against the seeded database, with a scripted model (no model API)."""

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator

import psycopg
import pytest
import uvicorn

from sr_agent.loop import AgentRun, run_agent
from sr_agent.model import ModelReply, ModelRequest
from sr_agent.testing import FakeModel, reply, text, tool_use
from sr_agent.toolbox import connect_http
from sr_mcp.adapters.postgres import PostgresReferralStore
from sr_mcp.http import build_app
from sr_mcp.server import create_server
from sr_mcp.testing import fake_deps
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.rows import World
from sr_simulator.write import write_world

pytestmark = pytest.mark.integration

TOKEN = "agent-integration-" + "k" * 32


@pytest.fixture
def world() -> World:
    return generate(SimConfig(seed=37, practices=4, clinics=6, referrals=150))


@pytest.fixture
def served(
    migrated: psycopg.Connection, reader_url: str, world: World
) -> Iterator[tuple[str, PostgresReferralStore]]:
    write_world(migrated, world)
    store = PostgresReferralStore(reader_url, mask_salt="agent")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = build_app(create_server(fake_deps(store=store)), TOKEN, host="127.0.0.1")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp", store
    server.should_exit = True
    thread.join(timeout=5)
    store.close()


def test_the_model_sees_what_the_store_holds(
    served: tuple[str, PostgresReferralStore], world: World
) -> None:
    url, store = served
    referral = next(r for r in world.referrals if r.hub_referral_id)
    seen: list[str] = []

    def answer(request: ModelRequest) -> ModelReply:
        seen.append(request.messages[-1]["content"][0]["content"])
        return reply(text("Answered from the evidence."))

    model = FakeModel(reply(tool_use("get_referral", referral_id=referral.id)), answer)

    async def ask() -> AgentRun:
        async with connect_http(url, TOKEN) as toolbox:
            return await run_agent(f"What happened to {referral.id}?", model=model, toolbox=toolbox)

    run = asyncio.run(ask())

    expected = store.get_referral(referral.id)
    assert expected is not None
    assert json.loads(seen[0])["referral"] == expected.model_dump(mode="json")
    assert run.outcome == "answered"
    assert not run.tool_calls[0].is_error
