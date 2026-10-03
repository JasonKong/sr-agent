"""HTTP transport: a real uvicorn server on a free port, with fake data (no database)."""

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from sr_mcp.cli import main
from sr_mcp.http import MIN_TOKEN_LENGTH, build_app
from sr_mcp.server import create_server
from sr_mcp.testing import fake_deps

TOKEN = "t" * 40


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    port = _free_port()
    app = build_app(create_server(fake_deps()), TOKEN, host="127.0.0.1")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _mcp(base_url: str, token: str, action: str) -> Any:
    async def run() -> Any:
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx2.AsyncClient(headers=headers) as http:
            transport = streamable_http_client(f"{base_url}/mcp", http_client=http)
            async with Client(transport) as client:
                if action == "tools":
                    return [t.name for t in (await client.list_tools()).tools]
                result = await client.call_tool("get_referral", {"referral_id": "GR-99999"})
                return result

    return asyncio.run(run())


def test_health_needs_no_token_and_says_nothing_else(base_url: str) -> None:
    response = httpx2.get(f"{base_url}/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token-" + "x" * 30},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": TOKEN},
        {"Authorization": f"Bearer {TOKEN[:-1]}"},
        {"Authorization": f"Bearer {TOKEN}{TOKEN}"},
    ],
)
def test_requests_without_the_right_token_are_refused(
    base_url: str, headers: dict[str, str]
) -> None:
    response = httpx2.post(f"{base_url}/mcp", headers=headers, json={})
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_the_right_token_reaches_the_tools(base_url: str) -> None:
    assert "get_referral" in _mcp(base_url, TOKEN, "tools")
    result = _mcp(base_url, TOKEN, "call")
    assert result.is_error  # the fake store is empty: a typed NOT_FOUND, over HTTP
    assert "NOT_FOUND" in result.content[0].text


def test_the_scheme_is_case_insensitive(base_url: str) -> None:
    assert "get_referral" in _mcp_with_header(base_url, f"bearer {TOKEN}")


def _mcp_with_header(base_url: str, authorization: str) -> Any:
    async def run() -> Any:
        async with httpx2.AsyncClient(headers={"Authorization": authorization}) as http:
            transport = streamable_http_client(f"{base_url}/mcp", http_client=http)
            async with Client(transport) as client:
                return [t.name for t in (await client.list_tools()).tools]

    return asyncio.run(run())


def test_auth_failures_are_logged_without_the_token(
    base_url: str, capfd: pytest.CaptureFixture[str]
) -> None:
    httpx2.post(f"{base_url}/mcp", headers={"Authorization": "Bearer nope"}, json={})
    time.sleep(0.1)
    err = capfd.readouterr().err
    lines = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    failures = [line for line in lines if line["event"] == "http_auth_failed"]
    assert failures
    assert failures[-1]["reason"] == "invalid"
    assert "nope" not in err


def test_short_tokens_are_rejected() -> None:
    with pytest.raises(ValueError, match=str(MIN_TOKEN_LENGTH)):
        build_app(create_server(fake_deps()), "short", host="127.0.0.1")


def test_http_will_not_start_without_a_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))  # no .env there
    monkeypatch.setenv("MCP_DATABASE_URL", "postgresql://u:p@localhost:1/none")
    monkeypatch.setenv("MCP_MASK_SALT", "x")
    monkeypatch.delenv("MCP_HTTP_TOKEN", raising=False)

    with pytest.raises(SystemExit):
        main(["--transport", "http"])
    assert "MCP_HTTP_TOKEN" in capsys.readouterr().err
