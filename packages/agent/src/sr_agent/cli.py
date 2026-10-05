"""`sra ask`: put a question to the diagnosis agent from the terminal.

The answer goes to stdout; a trace of each model and tool call goes to stderr as it
happens. `--json` prints the whole run record instead of the answer. The MCP server must
be running over HTTP (`make mcp-http`).

Exit codes: 0 answered, 1 a service is unavailable, 2 a usage or configuration error,
3 the run stopped without an answer (a limit, a refusal).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TextIO, get_args

from pydantic import ValidationError

from sr_agent.anthropic_model import AnthropicModel
from sr_agent.loop import (
    AgentEvent,
    AgentRun,
    Limits,
    ModelCalled,
    RunFinished,
    RunStarted,
    ToolCalled,
    json_log,
    run_agent,
)
from sr_agent.model import Effort, ModelClient, ModelUnavailable, TokenUsage
from sr_agent.settings import AgentSettings
from sr_agent.toolbox import Toolbox, ToolsUnavailable, connect_http

Connect = Callable[[str, str], AbstractAsyncContextManager[Toolbox]]


def _tokens(usage: TokenUsage) -> str:
    sent = usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens
    return f"{sent:,} in ({usage.cache_read_input_tokens:,} cached) / {usage.output_tokens:,} out"


class Trace:
    """Renders the loop's events for a person watching the terminal."""

    def __init__(self, out: TextIO) -> None:
        self._out = out

    def __call__(self, event: AgentEvent) -> None:
        match event:
            case RunStarted():
                line = f"sra · {event.model} · run {event.run_id}"
            case ModelCalled():
                line = (
                    f"  turn {event.turn} · {event.stop_reason} · "
                    f"{event.duration_ms / 1000:.1f} s · {_tokens(event.usage)}"
                )
            case ToolCalled():
                r = event.record
                error = " · error" if r.is_error else ""
                line = (
                    f"    → {r.name} {json.dumps(r.arguments)} · {r.result_chars:,} chars"
                    f" · {r.duration_ms:.0f} ms{error}"
                )
            case RunFinished():
                run = event.run
                calls = len(run.tool_calls)
                served = ", ".join(dict.fromkeys(run.served_by)) or "-"
                line = (
                    f"sra · {run.outcome} in {run.duration_ms / 1000:.1f} s · "
                    f"{calls} tool call{'s' if calls != 1 else ''} · {run.turns} turns · "
                    f"tokens {_tokens(run.usage)} · served by {served}"
                )
        print(line, file=self._out, flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sra", description="SR-Agent diagnosis agent.")
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="Ask why a referral failed or was not received.")
    ask.add_argument("question")
    ask.add_argument("--model", help="model id (default SRA_MODEL or claude-opus-5-5)")
    ask.add_argument("--effort", choices=get_args(Effort), help="default SRA_EFFORT or medium")
    ask.add_argument("--mcp-url", help="default SRA_MCP_URL or http://127.0.0.1:8765/mcp")
    ask.add_argument("--max-tool-calls", type=int, help="default SRA_MAX_TOOL_CALLS or 8")
    ask.add_argument("--time-limit", type=float, help="seconds; default SRA_TIME_LIMIT_S or 60")
    ask.add_argument("--json", action="store_true", help="print the whole run as JSON")
    ask.add_argument("--log-json", action="store_true", help="JSON log lines instead of a trace")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    model: ModelClient | None = None,
    connect: Connect = connect_http,
) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = AgentSettings()
    except ValidationError as error:
        print(f"sra: invalid configuration: {error}", file=sys.stderr)
        return 2
    if settings.mcp_token is None:
        print("sra: set MCP_HTTP_TOKEN (the MCP server's bearer token) in .env", file=sys.stderr)
        return 2

    if model is None:
        key = settings.anthropic_api_key
        try:
            model = AnthropicModel(
                model=args.model or settings.model,
                effort=args.effort or settings.effort,
                fallback=settings.fallback == "default",
                api_key=key.get_secret_value() if key else None,
                timeout_s=args.time_limit or settings.time_limit_s,
            )
        except ModelUnavailable as error:
            print(f"sra: {error}", file=sys.stderr)
            return 2
    limits = Limits(
        max_tool_calls=args.max_tool_calls or settings.max_tool_calls,
        time_limit_s=args.time_limit or settings.time_limit_s,
        token_budget=settings.token_budget,
    )
    observe = json_log if args.log_json else Trace(sys.stderr)
    url = args.mcp_url or settings.mcp_url
    token = settings.mcp_token.get_secret_value()

    async def ask() -> AgentRun:
        async with connect(url, token) as toolbox:
            return await run_agent(
                args.question, model=model, toolbox=toolbox, limits=limits, observe=observe
            )

    try:
        run = asyncio.run(ask())
    except (ToolsUnavailable, ModelUnavailable) as error:
        print(f"sra: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(run.model_dump_json(indent=2))
    elif run.answer:
        print(run.answer)
    return 0 if run.outcome == "answered" else 3
