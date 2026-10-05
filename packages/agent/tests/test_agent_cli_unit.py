"""`sra ask` with a FakeModel and the MCP server connected in-process."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import Client

from sr_agent.cli import main
from sr_agent.loop import AgentRun
from sr_agent.model import ModelUnavailable
from sr_agent.settings import AgentSettings
from sr_agent.testing import FakeModel, reply, text, tool_use
from sr_agent.toolbox import McpToolbox, ToolsUnavailable, open_toolbox
from sr_mcp.server import create_server
from sr_mcp.testing import fake_deps


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No .env and no SRA_ or token variables from the developer's shell."""
    monkeypatch.chdir(tmp_path)
    for name in ("MCP_HTTP_TOKEN", "ANTHROPIC_API_KEY", "SRA_MODEL", "SRA_EFFORT", "SRA_MCP_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MCP_HTTP_TOKEN", "t" * 40)


@asynccontextmanager
async def in_process(url: str, token: str) -> AsyncIterator[McpToolbox]:
    async with open_toolbox(Client(create_server(fake_deps()))) as toolbox:
        yield toolbox


def script() -> FakeModel:
    return FakeModel(
        reply(tool_use("list_failed_referrals", hours=24)),
        reply(text("No referrals failed in the last day.")),
    )


def test_ask_prints_the_answer_and_a_trace(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["ask", "Anything fail today?"], model=script(), connect=in_process)

    out, err = capsys.readouterr()
    assert code == 0
    assert out.strip() == "No referrals failed in the last day."
    assert "list_failed_referrals" in err
    assert '"hours": 24' in err
    assert "answered" in err
    assert "1 tool call" in err


def test_json_prints_the_whole_run(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["ask", "Anything fail today?", "--json"], model=script(), connect=in_process)

    run = AgentRun.model_validate_json(capsys.readouterr().out)
    assert code == 0
    assert run.outcome == "answered"
    assert run.question == "Anything fail today?"
    assert [c.name for c in run.tool_calls] == ["list_failed_referrals"]


def test_an_incomplete_run_says_why_and_exits_3(capsys: pytest.CaptureFixture[str]) -> None:
    model = FakeModel(*[reply(tool_use("list_failed_referrals")) for _ in range(3)])
    code = main(["ask", "q", "--max-tool-calls", "2"], model=model, connect=in_process)

    out, err = capsys.readouterr()
    assert code == 3
    assert out == ""
    assert "tool_limit" in err


def test_a_missing_token_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MCP_HTTP_TOKEN")
    code = main(["ask", "q"], model=script())

    assert code == 2
    assert "MCP_HTTP_TOKEN" in capsys.readouterr().err


@pytest.mark.parametrize(
    "failure",
    [ToolsUnavailable("cannot reach the MCP server"), ModelUnavailable("cannot reach the model")],
)
def test_unavailable_services_exit_1_with_one_line(
    failure: Exception, capsys: pytest.CaptureFixture[str]
) -> None:
    @asynccontextmanager
    async def broken(url: str, token: str) -> AsyncIterator[McpToolbox]:
        raise failure
        yield  # pragma: no cover

    code = main(["ask", "q"], model=script(), connect=broken)

    err = capsys.readouterr().err
    assert code == 1
    assert f"sra: {failure}" in err
    assert "Traceback" not in err


def test_settings_have_safe_defaults_and_read_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = AgentSettings()
    assert defaults.model == "claude-opus-5-5"
    assert defaults.effort == "medium"
    assert defaults.fallback == "default"
    assert defaults.mcp_url == "http://127.0.0.1:8765/mcp"
    assert (defaults.max_tool_calls, defaults.time_limit_s, defaults.token_budget) == (
        8,
        60,
        200_000,
    )
    assert defaults.anthropic_api_key is None

    monkeypatch.setenv("SRA_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("SRA_EFFORT", "low")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    changed = AgentSettings()
    assert changed.model == "claude-sonnet-5-5"
    assert changed.effort == "low"
    assert changed.anthropic_api_key is None  # an empty placeholder in .env means "not set"


def test_settings_reject_an_unknown_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRA_EFFORT", "extreme")
    with pytest.raises(ValueError, match="effort"):
        AgentSettings()


def test_settings_read_a_local_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_HTTP_TOKEN")
    (tmp_path / ".env").write_text("MCP_HTTP_TOKEN=from-the-file-" + "z" * 30 + "\n")
    settings = AgentSettings()
    assert settings.mcp_token is not None
    assert settings.mcp_token.get_secret_value().startswith("from-the-file-")
    assert json.loads(settings.model_dump_json())["mcp_token"] == "**********"


def test_missing_model_credentials_stop_before_connecting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    @asynccontextmanager
    async def must_not_connect(url: str, token: str) -> AsyncIterator[McpToolbox]:
        raise AssertionError("connected before checking the model credentials")
        yield  # pragma: no cover

    code = main(["ask", "q"], connect=must_not_connect)

    assert code == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
