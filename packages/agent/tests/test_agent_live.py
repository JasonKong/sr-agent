"""The model API for real. Run with `uv run pytest -m live`; all of it costs about $0.20.

The MCP server runs in-process with an empty fake store, so the tests need only model
credentials (ANTHROPIC_API_KEY in the environment or .env). Each candidate model is tested
with the request its profile builds (`sr_agent.model_profiles`).
"""

import asyncio

import pytest
from mcp import Client

from sr_agent.anthropic_model import AnthropicModel, model_from_settings
from sr_agent.loop import AgentRun, Limits, run_agent
from sr_agent.model import Effort, ModelRequest
from sr_agent.model_profiles import PROFILES, profile_for
from sr_agent.settings import AgentSettings
from sr_agent.toolbox import open_toolbox
from sr_mcp.server import create_server
from sr_mcp.testing import fake_deps

pytestmark = pytest.mark.live

MODELS = list(PROFILES)


def cheapest(name: str) -> AnthropicModel:
    """The model at its lowest effort, where it has effort levels."""
    effort: Effort | None = "low" if profile_for(name).effort else None
    return model_from_settings(AgentSettings(), model=name, effort=effort)


@pytest.mark.parametrize("name", MODELS)
def test_each_model_accepts_the_request_its_profile_builds(name: str) -> None:
    """A dummy question with a tiny tool and schema: about a cent for all three models."""
    request = ModelRequest(
        system="You answer in the JSON format asked for.",
        tools=[
            {
                "name": "ping",
                "description": "Returns pong.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        messages=[{"role": "user", "content": "Say hello. You do not need the tool."}],
        max_tokens=8_000,
        output_schema={
            "type": "object",
            "properties": {"greeting": {"type": "string"}},
            "required": ["greeting"],
            "additionalProperties": False,
        },
    )
    reply = asyncio.run(cheapest(name).complete(request))

    assert reply.stop_reason in ("end_turn", "tool_use"), reply
    assert reply.model.startswith(name.rsplit("-", 2)[0])  # served by its own family


@pytest.mark.parametrize("name", MODELS)
def test_a_real_model_uses_a_tool_and_answers_with_a_cached_prefix(name: str) -> None:
    model = cheapest(name)

    async def ask() -> AgentRun:
        async with open_toolbox(Client(create_server(fake_deps()))) as toolbox:
            return await run_agent(
                "Which referrals failed in the last 24 hours? Check with the tools before you "
                "answer.",
                model=model,
                toolbox=toolbox,
                limits=Limits(max_tool_calls=4, time_limit_s=120),
            )

    run = asyncio.run(ask())

    assert run.outcome == "answered", run.model_dump_json(indent=2)
    assert run.diagnosis is not None  # structured output, checked by the loop
    assert run.turns >= 2  # a tool call, then the answer: thinking blocks were replayed
    assert any(c.name == "list_failed_referrals" for c in run.tool_calls)
    assert run.usage.cache_read_input_tokens > 0  # the second turn read the cached prefix
    assert all(run.served_by)


def test_a_second_run_reads_the_shared_prefix_on_its_first_turn() -> None:
    """The breakpoint after the system prompt lets every case reuse the tools and prompt."""
    model = cheapest("claude-opus-5-5")

    async def ask() -> AgentRun:
        async with open_toolbox(Client(create_server(fake_deps()))) as toolbox:
            return await run_agent(
                "Did anything fail in the last hour?",
                model=model,
                toolbox=toolbox,
                limits=Limits(max_tool_calls=2, time_limit_s=120),
            )

    asyncio.run(ask())
    second = asyncio.run(ask())

    assert second.turn_usage[0].usage.cache_read_input_tokens > 0, second.turn_usage
