"""McpToolbox over the real MCP server, connected in-process (fake data, no network)."""

import asyncio
import json
from typing import Any

import pytest
from mcp import Client

from sr_agent.model import ModelUnavailable
from sr_agent.toolbox import McpToolbox, ToolOutcome, open_toolbox, without
from sr_mcp.server import INSTRUCTIONS, create_server
from sr_mcp.testing import fake_deps


def with_toolbox(action: Any) -> Any:
    async def run() -> Any:
        async with open_toolbox(Client(create_server(fake_deps()))) as toolbox:
            return await action(toolbox)

    return asyncio.run(run())


def test_every_server_tool_becomes_a_model_tool_definition() -> None:
    async def specs(toolbox: McpToolbox) -> Any:
        return toolbox.specs()

    async def server_tools() -> Any:
        async with Client(create_server(fake_deps())) as client:
            return (await client.list_tools()).tools

    tools = asyncio.run(server_tools())
    defined = with_toolbox(specs)

    assert [d["name"] for d in defined] == [t.name for t in tools]
    for definition, tool in zip(defined, tools, strict=True):
        assert set(definition) == {"name", "description", "input_schema"}
        assert definition["description"] == tool.description
        assert definition["input_schema"] == tool.input_schema
    get_referral = next(d for d in defined if d["name"] == "get_referral")
    assert get_referral["input_schema"]["required"] == ["referral_id"]


def test_the_server_instructions_come_with_the_tools() -> None:
    async def instructions(toolbox: McpToolbox) -> str:
        return toolbox.instructions

    assert with_toolbox(instructions) == INSTRUCTIONS


def test_a_result_is_sent_as_compact_json_of_the_structured_content() -> None:
    async def call(toolbox: McpToolbox) -> ToolOutcome:
        return await toolbox.call("list_failed_referrals", {})

    outcome = with_toolbox(call)

    assert not outcome.is_error
    data = json.loads(outcome.text)
    assert data["failures"] == []
    assert "as_of" in data
    assert "\n" not in outcome.text
    assert ": " not in outcome.text


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("get_referral", {"referral_id": "GR-99999"}, "NOT_FOUND"),
        ("get_referral", {"referral_id": "nonsense"}, "pattern"),
        ("get_referral", {}, "Field required"),
        ("drop_tables", {}, "Unknown tool"),
    ],
)
def test_tool_errors_come_back_as_error_outcomes(
    name: str, arguments: dict[str, Any], expected: str
) -> None:
    async def call(toolbox: McpToolbox) -> ToolOutcome:
        return await toolbox.call(name, arguments)

    outcome = with_toolbox(call)

    assert outcome.is_error
    assert expected in outcome.text


def test_exceptions_from_the_caller_come_out_unwrapped() -> None:
    async def fail(toolbox: McpToolbox) -> None:
        raise ModelUnavailable("the model API refused the API key")

    with pytest.raises(ModelUnavailable, match="refused the API key"):
        with_toolbox(fail)


def test_a_toolbox_can_hide_tools_for_an_ablation() -> None:
    async def hidden(toolbox: McpToolbox) -> Any:
        smaller = without(toolbox, {"search_logs", "no_such_tool"})
        names = [d["name"] for d in smaller.specs()]
        refused = await smaller.call("search_logs", {"referral_id": "GR-1"})
        allowed = await smaller.call("list_failed_referrals", {})
        return names, refused, allowed, smaller.instructions

    names, refused, allowed, instructions = with_toolbox(hidden)

    assert "search_logs" not in names
    assert "get_referral" in names
    assert refused.is_error
    assert "not available" in refused.text
    assert not allowed.is_error
    assert instructions == INSTRUCTIONS
