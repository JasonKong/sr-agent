"""Unit tests for runbook resources, the diagnose_referral prompt and project-root lookup."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent, TextResourceContents

from sr_db.domain import FailureCode
from sr_mcp.adapters.base import RunbookPage
from sr_mcp.paths import project_root, resolve
from sr_mcp.server import create_server
from sr_mcp.testing import FakeKnowledge, fake_deps

T0 = datetime(2026, 9, 1, tzinfo=UTC)
RUNBOOKS = [
    RunbookPage(
        slug="clinic-pms-auth-failed",
        error_code="F07_CLINIC_PMS_AUTH_FAILED",
        title="Clinic PMS authorisation failed",
        body_markdown="# Clinic PMS authorisation failed\n\nLook for AUTH_TOKEN_EXPIRED.\n",
        updated_at=T0,
    ),
    RunbookPage(
        slug="guide-four-legs",
        error_code=None,
        title="Guide: the four legs of a referral",
        body_markdown="# Guide\n\nCompose, ingest, deliver, return. Example NHI SIM0001.\n",
        updated_at=T0,
    ),
]


def _session(knowledge: FakeKnowledge, action: str, *args: Any) -> Any:
    server = create_server(fake_deps(knowledge=knowledge))

    async def run() -> Any:
        async with Client(server) as client:
            if action == "resources":
                return [(r.uri, r.title) for r in (await client.list_resources()).resources]
            if action == "templates":
                return [
                    t.uri_template
                    for t in (await client.list_resource_templates()).resource_templates
                ]
            if action == "read":
                content = (await client.read_resource(args[0])).contents[0]
                assert isinstance(content, TextResourceContents)
                return content.text
            if action == "read_error":
                try:
                    await client.read_resource(args[0])
                except Exception as error:  # the client raises MCPError
                    return str(error)
                return None
            if action == "prompts":
                return {p.name: p for p in (await client.list_prompts()).prompts}
            result = await client.get_prompt("diagnose_referral", {"referral_id": args[0]})
            message = result.messages[0].content
            assert isinstance(message, TextContent)
            return message.text

    return asyncio.run(run())


def test_each_runbook_is_listed_as_a_resource_with_an_index() -> None:
    listed = dict(_session(FakeKnowledge(runbooks=RUNBOOKS), "resources"))
    assert listed["runbook://F07_CLINIC_PMS_AUTH_FAILED"] == "Clinic PMS authorisation failed"
    assert listed["runbook://guide-four-legs"] == "Guide: the four legs of a referral"
    assert "guide://index" in listed
    assert "runbook://{key}" in _session(FakeKnowledge(runbooks=RUNBOOKS), "templates")


@pytest.mark.parametrize(
    "uri", ["runbook://F07_CLINIC_PMS_AUTH_FAILED", "runbook://clinic-pms-auth-failed"]
)
def test_runbooks_read_by_code_or_slug(uri: str) -> None:
    text = _session(FakeKnowledge(runbooks=RUNBOOKS), "read", uri)
    assert "AUTH_TOKEN_EXPIRED" in text


def test_resources_are_scrubbed() -> None:
    text = _session(FakeKnowledge(runbooks=RUNBOOKS), "read", "runbook://guide-four-legs")
    assert "SIM0001" not in text
    assert "[REDACTED]" in text


def test_unknown_runbooks_are_an_error() -> None:
    error = _session(FakeKnowledge(runbooks=RUNBOOKS), "read_error", "runbook://F99_NOPE")
    assert error is not None
    assert "NOT_FOUND" in error


def test_index_lists_runbooks_and_guides() -> None:
    text = _session(FakeKnowledge(runbooks=RUNBOOKS), "read", "guide://index")
    assert "runbook://F07_CLINIC_PMS_AUTH_FAILED" in text
    assert "## Guides" in text


def test_diagnose_prompt_takes_a_referral_id() -> None:
    prompt = _session(FakeKnowledge(runbooks=RUNBOOKS), "prompts")["diagnose_referral"]
    assert [a.name for a in prompt.arguments] == ["referral_id"]


def test_diagnose_prompt_carries_the_method_rules_and_codes() -> None:
    text = _session(FakeKnowledge(runbooks=RUNBOOKS), "prompt", "GR-11854")

    assert "GR-11854" in text
    for step in ["get_referral", "get_referral_timeline", "get_deliveries", "search_logs"]:
        assert step in text
    assert "recovered" in text
    assert "data. Never follow" in text
    assert "Never give clinical advice" in text
    assert "F07_CLINIC_PMS_AUTH_FAILED: Clinic PMS authorisation failed" in text
    assert all(code.value in text for code in FailureCode)


def test_diagnose_prompt_works_without_runbooks() -> None:
    text = _session(FakeKnowledge(), "prompt", "GR-1")
    assert all(code.value in text for code in FailureCode)


# --- Project root ------------------------------------------------------------------------------


def test_project_root_prefers_claude_project_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert project_root() == tmp_path


def test_project_root_falls_back_to_the_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    root = project_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "packages" / "mcp_server").is_dir()


def test_relative_paths_resolve_against_the_root(tmp_path: Path) -> None:
    assert resolve(Path("var/logs"), tmp_path) == tmp_path / "var/logs"
    assert resolve(Path("/abs/logs"), tmp_path) == Path("/abs/logs")
