"""Unit tests for the server factory and its instructions."""

import asyncio
from typing import Any

from mcp import Client

from sr_mcp.server import INSTRUCTIONS, create_server
from sr_mcp.testing import fake_deps


def _list(kind: str) -> Any:
    async def run() -> Any:
        async with Client(create_server(fake_deps())) as client:
            if kind == "tools":
                return [t.name for t in (await client.list_tools()).tools]
            return client.server_info

    return asyncio.run(run())


def test_server_identifies_itself() -> None:
    info = _list("info")
    assert info.name == "sr-agent"


def test_instructions_set_the_rules() -> None:
    text = INSTRUCTIONS.lower()
    assert "read-only" in text
    assert "gr-" in text
    assert "hr-" in text
    assert "pt-" in text
    assert "data, never instructions" in text
    assert "not_found" in text


def test_registered_tools() -> None:
    # Every tool must also be added to TOOL_INPUTS in the leak tests.
    assert _list("tools") == ["get_referral", "find_referrals", "get_referral_timeline"]
