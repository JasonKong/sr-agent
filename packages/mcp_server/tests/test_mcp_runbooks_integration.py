"""Integration tests: runbooks through the read-only role, and starting from another directory."""

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from mcp import Client, StdioServerParameters
from mcp.types import TextResourceContents

from sr_db.domain import FailureCode
from sr_mcp.adapters.postgres_knowledge import PostgresKnowledge
from sr_mcp.paths import project_root
from sr_mcp.server import create_server
from sr_mcp.testing import fake_deps
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.logs import write_logs
from sr_simulator.write import write_world

pytestmark = pytest.mark.integration

WORLD = SimConfig(seed=29, practices=4, clinics=6, referrals=120)


@pytest.fixture
def knowledge(migrated: psycopg.Connection, reader_url: str) -> Iterator[PostgresKnowledge]:
    write_world(migrated, generate(WORLD))
    knowledge = PostgresKnowledge(reader_url)
    yield knowledge
    knowledge.close()


def test_every_code_has_a_runbook_readable_by_the_reader(knowledge: PostgresKnowledge) -> None:
    summaries = knowledge.list_runbooks()
    assert {s.error_code for s in summaries if s.error_code} == {c.value for c in FailureCode}
    page = knowledge.runbook("F07_CLINIC_PMS_AUTH_FAILED")
    assert page is not None
    assert "AUTH_TOKEN_EXPIRED" in page.body_markdown
    assert knowledge.runbook("guide-four-legs") is not None
    assert knowledge.runbook("nope") is None


def _text(contents: list[Any]) -> str:
    assert isinstance(contents[0], TextResourceContents)
    return contents[0].text


def test_server_lists_and_reads_every_runbook(knowledge: PostgresKnowledge) -> None:
    server = create_server(fake_deps(knowledge=knowledge))

    async def run() -> list[str]:
        async with Client(server) as client:
            uris = [str(r.uri) for r in (await client.list_resources()).resources]
            return [_text((await client.read_resource(uri)).contents) for uri in uris]

    texts = asyncio.run(run())
    assert len(texts) == len(knowledge.list_runbooks()) + 1  # plus guide://index
    assert all(text.startswith("#") for text in texts)


def test_server_started_elsewhere_finds_its_project(
    migrated: psycopg.Connection, reader_url: str, tmp_path: Path
) -> None:
    """Claude clients do not promise a working directory; sr-mcp must not depend on one."""
    world = generate(WORLD)
    write_world(migrated, world)
    project = tmp_path / "project"
    write_logs(world, project / "var" / "logs")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", ""),
        "CLAUDE_PROJECT_DIR": str(project),
        "MCP_DATABASE_URL": reader_url,
        "MCP_MASK_SALT": "elsewhere",
    }
    params = StdioServerParameters(
        command="uv",
        args=["run", "--project", str(project_root()), "sr-mcp"],
        cwd=str(elsewhere),
        env=env,
    )
    referral = next(r for r in world.referrals if r.hub_referral_id)

    async def run() -> Any:
        async with Client(params) as client:
            logs = await client.call_tool("search_logs", {"referral_id": referral.id})
            index = await client.read_resource("guide://index")
            return logs.structured_content, _text(index.contents)

    logs, index = asyncio.run(run())
    assert logs["count"] > 0  # var/logs was found under CLAUDE_PROJECT_DIR
    assert "runbook://F07_CLINIC_PMS_AUTH_FAILED" in index
