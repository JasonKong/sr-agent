"""Unit tests for the tool guard: scrubbing, size caps, typed errors and the call log.

Uses a test-only tool on a real MCPServer, through the SDK's in-memory client.
"""

import asyncio
import json
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

from sr_mcp.guard import NotFound, ToolResult, guarded_tool
from sr_mcp.server import create_server
from sr_mcp.testing import fake_deps


class Notes(ToolResult):
    referral_id: str
    notes: list[str]


def _server(max_chars: int = 8000) -> MCPServer:
    deps = fake_deps(max_result_chars=max_chars)
    server = create_server(deps)

    @guarded_tool(server, deps)
    def get_notes(referral_id: str, count: int = 3) -> Notes:
        """Return test notes."""
        if referral_id == "GR-404":
            raise NotFound("referral", referral_id)
        notes = [f"note {n}: patient SIM0{n:03d}" if n == 1 else f"note {n}" for n in range(count)]
        return Notes(referral_id=referral_id, notes=notes)

    return server


def _call(server: MCPServer, name: str, args: dict[str, Any]) -> Any:
    async def run() -> Any:
        async with Client(server) as client:
            return await client.call_tool(name, args)

    return asyncio.run(run())


def _tools(server: MCPServer) -> Any:
    async def run() -> Any:
        async with Client(server) as client:
            return (await client.list_tools()).tools

    return asyncio.run(run())


def test_guarded_tool_keeps_its_schema() -> None:
    (tool,) = [t for t in _tools(_server()) if t.name == "get_notes"]

    assert set(tool.input_schema["properties"]) == {"referral_id", "count"}
    assert {"truncated", "redacted", "notes"} <= set(tool.output_schema["properties"])


def test_results_are_scrubbed_and_count_redactions() -> None:
    result = _call(_server(), "get_notes", {"referral_id": "GR-1"})

    assert not result.is_error
    data = result.structured_content
    assert data["redacted"] == 1
    assert "SIM0001" not in json.dumps(data)
    assert data["truncated"] is False


def test_large_results_are_cut_and_marked() -> None:
    result = _call(_server(max_chars=400), "get_notes", {"referral_id": "GR-1", "count": 200})

    data = result.structured_content
    assert data["truncated"] is True
    assert 0 < len(data["notes"]) < 200
    assert len(json.dumps(data)) <= 400
    assert data["notes"][0] == "note 0"  # keeps the first items, drops from the end


def test_unknown_ids_are_a_typed_error_not_an_empty_success() -> None:
    result = _call(_server(), "get_notes", {"referral_id": "GR-404"})

    assert result.is_error
    assert "NOT_FOUND" in result.content[0].text
    assert "GR-404" in result.content[0].text


def test_every_call_is_logged_to_stderr_without_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _call(_server(), "get_notes", {"referral_id": "GR-1"})
    _call(_server(), "get_notes", {"referral_id": "GR-404"})

    captured = capsys.readouterr()
    assert captured.out == ""  # stdout belongs to the stdio protocol
    lines = [json.loads(line) for line in captured.err.splitlines() if line.startswith("{")]
    calls = [line for line in lines if line.get("event") == "tool_call"]
    assert [c["outcome"] for c in calls] == ["ok", "not_found"]
    first = calls[0]
    assert {"tool", "caller", "duration_ms", "result_chars", "redacted"} <= set(first)
    assert first["tool"] == "get_notes"
    assert first["redacted"] == 1
    logged = json.dumps(first)
    assert "note 0" not in logged  # never the result content
    assert "GR-1" not in logged  # nor the arguments
