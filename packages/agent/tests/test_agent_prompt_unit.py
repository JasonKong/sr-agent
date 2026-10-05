"""The system prompt: complete, consistent with the server's tools, and stable."""

import asyncio
import re

from mcp import Client

from sr_agent.prompts import PROMPT_VERSION, build_system_prompt
from sr_db.domain import FailureCode
from sr_mcp.server import INSTRUCTIONS, create_server
from sr_mcp.testing import fake_deps


def server_tool_names() -> set[str]:
    async def names() -> set[str]:
        async with Client(create_server(fake_deps())) as client:
            return {t.name for t in (await client.list_tools()).tools}

    return asyncio.run(names())


def test_every_tool_the_prompt_names_exists_on_the_server() -> None:
    prompt = build_system_prompt(INSTRUCTIONS)
    named = set(re.findall(r"\b(?:get|find|search|list)_[a-z_]+\b", prompt))

    assert named  # the method names tools
    assert named <= server_tool_names()


def test_every_root_cause_code_is_listed() -> None:
    prompt = build_system_prompt(INSTRUCTIONS)
    for code in FailureCode:
        assert code.value in prompt


def test_the_server_instructions_are_included_verbatim() -> None:
    assert INSTRUCTIONS.strip() in build_system_prompt(INSTRUCTIONS)


def test_the_prompt_is_stable_so_it_can_be_cached() -> None:
    assert build_system_prompt(INSTRUCTIONS) == build_system_prompt(INSTRUCTIONS)
    assert PROMPT_VERSION == "v1"
