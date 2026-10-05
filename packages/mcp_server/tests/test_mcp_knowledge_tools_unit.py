"""search_knowledge and get_runbook over fake knowledge (no database)."""

import asyncio
from datetime import UTC, datetime
from typing import Any

from mcp import Client

from sr_db.domain import KnowledgeSource
from sr_mcp.adapters.base import KnowledgeHit, RunbookPage
from sr_mcp.server import create_server
from sr_mcp.testing import FakeKnowledge, fake_deps

HITS = [
    KnowledgeHit(
        chunk_id=f"tkt_{n}#0",
        source_type=KnowledgeSource.TICKET,
        source_id=f"tkt_{n}",
        title=f"Ticket {n}",
        text=f"Patient SIM00{n}0 again. Resolved by re-authorising.",
        error_code="F07_CLINIC_PMS_AUTH_FAILED" if n % 2 else "F08_CLINIC_PMS_TIMEOUT",
        score=1 / n,
    )
    for n in range(1, 8)
]
RUNBOOK = RunbookPage(
    slug="clinic-pms-auth-failed",
    error_code="F07_CLINIC_PMS_AUTH_FAILED",
    title="Clinic PMS authorisation failed",
    body_markdown="# Clinic PMS authorisation failed\n\n## Symptoms",
    updated_at=datetime(2026, 9, 1, tzinfo=UTC),
)


def call(tool: str, arguments: dict[str, Any]) -> Any:
    server = create_server(fake_deps(knowledge=FakeKnowledge(hits=HITS, runbooks=[RUNBOOK])))

    async def run() -> Any:
        async with Client(server) as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(run())


def test_search_returns_at_most_top_k_hits_with_their_sources() -> None:
    result = call("search_knowledge", {"query": "PMS token expired", "top_k": 3})

    hits = result.structured_content["hits"]
    assert [h["source_id"] for h in hits] == ["tkt_1", "tkt_2", "tkt_3"]
    assert hits[0]["source_type"] == "TICKET"
    assert hits[0]["chunk_id"] == "tkt_1#0"


def test_search_can_be_narrowed_to_one_code() -> None:
    result = call(
        "search_knowledge", {"query": "token", "error_code": "F08_CLINIC_PMS_TIMEOUT", "top_k": 5}
    )
    codes = {h["error_code"] for h in result.structured_content["hits"]}
    assert codes == {"F08_CLINIC_PMS_TIMEOUT"}


def test_search_results_are_scrubbed_like_every_tool_result() -> None:
    result = call("search_knowledge", {"query": "token"})
    assert "SIM0010" not in str(result.structured_content)
    assert result.structured_content["redacted"] > 0


def test_top_k_is_capped_at_five_and_the_query_must_say_something() -> None:
    assert call("search_knowledge", {"query": "token", "top_k": 6}).is_error
    assert call("search_knowledge", {"query": ""}).is_error
    assert call("search_knowledge", {"query": "token", "error_code": "F99"}).is_error


def test_get_runbook_by_code_or_not_found() -> None:
    found = call("get_runbook", {"error_code": "F07_CLINIC_PMS_AUTH_FAILED"})
    assert found.structured_content["runbook"]["title"] == "Clinic PMS authorisation failed"
    missing = call("get_runbook", {"error_code": "F06_MANUAL_ROUTING"})
    assert missing.is_error
    assert "NOT_FOUND" in missing.content[0].text


def test_the_descriptions_say_knowledge_is_context_not_evidence() -> None:
    async def tools() -> dict[str, str]:
        async with Client(create_server(fake_deps())) as client:
            return {t.name: t.description or "" for t in (await client.list_tools()).tools}

    descriptions = asyncio.run(tools())
    assert "never" in descriptions["search_knowledge"].lower()
    assert "different cause" in descriptions["search_knowledge"]
    assert "checklist" in descriptions["get_runbook"]
