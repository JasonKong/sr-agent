"""The system prompt: complete, consistent with the server's tools, and stable."""

import asyncio
import re

import pytest
from mcp import Client

from sr_agent.prompts import PROMPT_VERSION, PROMPT_VERSIONS, build_system_prompt
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


def test_the_knowledge_step_appears_only_with_the_knowledge_tools() -> None:
    with_rag = build_system_prompt(INSTRUCTIONS, server_tool_names())
    without_rag = build_system_prompt(
        INSTRUCTIONS, server_tool_names() - {"search_knowledge", "get_runbook"}
    )

    assert "search_knowledge" in with_rag
    assert "get_runbook" in with_rag
    assert "context, never evidence" in with_rag
    assert "search_knowledge" not in without_rag
    assert "get_runbook" not in without_rag


def test_prompt_v2_adds_decision_rules_to_v1() -> None:
    v1 = build_system_prompt(INSTRUCTIONS, server_tool_names(), version="v1")
    v2 = build_system_prompt(INSTRUCTIONS, server_tool_names(), version="v2")

    assert v1 == build_system_prompt(INSTRUCTIONS, server_tool_names())
    for rule in ("7 days", "24 hours", "token_expires_at", "as_of"):
        assert rule in v2
        assert rule not in v1
    assert set(PROMPT_VERSIONS) == {"v1", "v2"}


def test_an_unknown_prompt_version_is_refused() -> None:
    with pytest.raises(ValueError, match="v9"):
        build_system_prompt(INSTRUCTIONS, version="v9")
