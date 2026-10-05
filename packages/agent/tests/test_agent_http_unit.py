"""connect_http against a real uvicorn MCP server on a free port, with fake data."""

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import uvicorn

from sr_agent.model import ModelUnavailable
from sr_agent.toolbox import McpToolbox, ToolsUnavailable, connect_http
from sr_mcp.http import build_app
from sr_mcp.server import create_server
from sr_mcp.testing import fake_deps

TOKEN = "agent-test-" + "k" * 32


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def mcp_url() -> Iterator[str]:
    port = _free_port()
    app = build_app(create_server(fake_deps()), TOKEN, host="127.0.0.1")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


def connect(url: str, token: str, action: Any) -> Any:
    async def run() -> Any:
        async with connect_http(url, token) as toolbox:
            return await action(toolbox)

    return asyncio.run(run())


def test_the_right_token_reaches_the_tools(mcp_url: str) -> None:
    async def use(toolbox: McpToolbox) -> Any:
        return [d["name"] for d in toolbox.specs()], await toolbox.call(
            "get_referral", {"referral_id": "GR-99999"}
        )

    names, outcome = connect(mcp_url, TOKEN, use)

    assert "get_referral" in names
    assert outcome.is_error
    assert "NOT_FOUND" in outcome.text


def test_a_wrong_token_is_reported_as_such(mcp_url: str) -> None:
    wrong = "wrong-" + "x" * 40

    async def use(toolbox: McpToolbox) -> None:
        return None

    with pytest.raises(ToolsUnavailable) as error:
        connect(mcp_url, wrong, use)

    message = str(error.value)
    assert "401" in message
    assert "MCP_HTTP_TOKEN" in message
    assert wrong not in message


def test_a_missing_server_is_reported_with_its_url() -> None:
    url = f"http://127.0.0.1:{_free_port()}/mcp"

    async def use(toolbox: McpToolbox) -> None:
        return None

    with pytest.raises(ToolsUnavailable, match="cannot reach the MCP server") as error:
        connect(url, TOKEN, use)

    assert url in str(error.value)
    assert "make mcp-http" in str(error.value)


def test_exceptions_from_the_caller_are_not_mistaken_for_server_errors(mcp_url: str) -> None:
    async def use(toolbox: McpToolbox) -> None:
        raise ModelUnavailable("cannot reach the model API")

    with pytest.raises(ModelUnavailable):
        connect(mcp_url, TOKEN, use)
