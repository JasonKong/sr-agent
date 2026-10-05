"""The loop against the real model API. Run with `uv run pytest -m live`; costs a few cents.

The MCP server runs in-process with an empty fake store, so the test needs only model
credentials (ANTHROPIC_API_KEY in the environment or .env).
"""

import asyncio

import pytest
from mcp import Client

from sr_agent.anthropic_model import AnthropicModel
from sr_agent.loop import AgentRun, Limits, run_agent
from sr_agent.settings import AgentSettings
from sr_agent.toolbox import open_toolbox
from sr_mcp.server import create_server
from sr_mcp.testing import fake_deps

pytestmark = pytest.mark.live


def test_a_real_model_uses_a_tool_and_answers_with_a_cached_prefix() -> None:
    settings = AgentSettings()
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    model = AnthropicModel(
        model=settings.model, effort="low", fallback=settings.fallback == "default", api_key=key
    )

    async def ask() -> AgentRun:
        async with open_toolbox(Client(create_server(fake_deps()))) as toolbox:
            return await run_agent(
                "Which referrals failed in the last 24 hours? Check with the tools, then answer "
                "in one sentence.",
                model=model,
                toolbox=toolbox,
                limits=Limits(max_tool_calls=4, time_limit_s=120),
            )

    run = asyncio.run(ask())

    assert run.outcome == "answered", run.model_dump_json(indent=2)
    assert run.answer
    assert run.turns >= 2  # a tool call, then the answer: thinking blocks were replayed
    assert any(c.name == "list_failed_referrals" for c in run.tool_calls)
    assert run.usage.cache_read_input_tokens > 0  # the second turn read the cached prefix
    assert all(run.served_by)
