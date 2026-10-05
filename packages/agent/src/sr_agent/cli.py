"""`sra`: ask the diagnosis agent from the terminal (`sra ask`), or serve its HTTP API
(`sra serve`, `sra openapi` for its schema).

The answer goes to stdout; a trace of each model and tool call goes to stderr as it
happens. `--json` prints the whole run record instead of the answer. The MCP server must
be running over HTTP (`make mcp-http`).

Exit codes: 0 answered, 1 a service is unavailable, 2 a usage or configuration error,
3 the run is incomplete (a limit, a refusal); a partial answer is still printed.
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
from sr_agent.api import create_app
from sr_agent.audit import AuditStore, MemoryAudit, PostgresAudit
from sr_agent.diagnosis import Diagnosis
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
from sr_agent.prompts import PROMPT_VERSIONS
from sr_agent.settings import AgentSettings
from sr_agent.toolbox import Toolbox, ToolsUnavailable, connect_http

Connect = Callable[[str, str], AbstractAsyncContextManager[Toolbox]]


def _tokens(usage: TokenUsage) -> str:
    sent = usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens
    return f"{sent:,} in ({usage.cache_read_input_tokens:,} cached) / {usage.output_tokens:,} out"


def render(diagnosis: Diagnosis, outcome: str) -> str:
    """The diagnosis as a CS team member reads it."""
    lines = [] if outcome == "answered" else [f"INCOMPLETE ({outcome}): a partial answer.", ""]
    code = diagnosis.root_cause_code or "not enough evidence"
    lines += [
        diagnosis.summary,
        "",
        f"Root cause: {code} (confidence: {diagnosis.confidence})",
        "Evidence:",
    ]
    lines += [
        f"  - {e.record_id} ({e.record_type}, {e.system}): {e.detail}" for e in diagnosis.evidence
    ] or ["  (none)"]
    lines += [
        f"Next step: {diagnosis.recommended_action}",
        f"Escalate to engineering: {'yes' if diagnosis.escalate_to_engineering else 'no'}",
    ]
    if diagnosis.draft_customer_reply:
        lines += ["", "Draft reply:", diagnosis.draft_customer_reply]
    return "\n".join(lines)


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
                cost = f" · ${run.cost_usd:.3f}" if run.cost_usd is not None else ""
                line = (
                    f"sra · {run.outcome} in {run.duration_ms / 1000:.1f} s · "
                    f"{calls} tool call{'s' if calls != 1 else ''} · {run.turns} turns · "
                    f"tokens {_tokens(run.usage)}{cost} · served by {served}"
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
    ask.add_argument("--prompt", choices=sorted(PROMPT_VERSIONS), help="default SRA_PROMPT or v1")
    ask.add_argument("--max-tool-calls", type=int, help="default SRA_MAX_TOOL_CALLS or 8")
    ask.add_argument("--time-limit", type=float, help="seconds; default SRA_TIME_LIMIT_S or 60")
    ask.add_argument("--json", action="store_true", help="print the whole run as JSON")
    ask.add_argument("--log-json", action="store_true", help="JSON log lines instead of a trace")
    serve = commands.add_parser("serve", help="run the HTTP API for the CS console")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8766)
    commands.add_parser("openapi", help="print the HTTP API's OpenAPI schema")
    return parser


def _serve(args: argparse.Namespace, settings: AgentSettings) -> int:
    import uvicorn

    if settings.mcp_token is None:
        print("sra: set MCP_HTTP_TOKEN (the MCP server's bearer token) in .env", file=sys.stderr)
        return 2
    key = settings.anthropic_api_key
    try:
        model = AnthropicModel(
            model=settings.model,
            effort=settings.effort,
            fallback=settings.fallback == "default",
            api_key=key.get_secret_value() if key else None,
            timeout_s=settings.time_limit_s,
        )
    except ModelUnavailable as error:
        print(f"sra: {error}", file=sys.stderr)
        return 2
    audit_url = settings.audit_database_url
    audit: AuditStore = PostgresAudit(audit_url.get_secret_value()) if audit_url else MemoryAudit()
    if audit_url is None:
        print(
            "sra: no SRA_AUDIT_DATABASE_URL, so diagnoses are kept in memory only", file=sys.stderr
        )
    app = create_app(
        model_for=lambda: model,
        connect=connect_http,
        audit=audit,
        mcp_url=settings.mcp_url,
        mcp_token=settings.mcp_token.get_secret_value(),
        api_token=settings.api_token.get_secret_value() if settings.api_token else None,
        limits=Limits(
            max_tool_calls=settings.max_tool_calls,
            time_limit_s=settings.time_limit_s,
            token_budget=settings.token_budget,
        ),
        prompt_version=settings.prompt,
        max_concurrent=settings.api_max_concurrent,
        cors_origins=[o.strip() for o in settings.api_cors_origins.split(",") if o.strip()],
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def openapi_schema() -> dict[str, object]:
    """The API's OpenAPI schema, without starting it or needing any credentials."""

    def no_model() -> ModelClient:
        raise RuntimeError("the schema needs no model")

    app = create_app(
        model_for=no_model,
        connect=connect_http,
        audit=MemoryAudit(),
        mcp_url="",
        mcp_token="",
        api_token=None,
    )
    return app.openapi()


def main(
    argv: list[str] | None = None,
    *,
    model: ModelClient | None = None,
    connect: Connect = connect_http,
) -> int:
    args = _parser().parse_args(argv)
    if args.command == "openapi":
        print(json.dumps(openapi_schema(), indent=2))
        return 0
    try:
        settings = AgentSettings()
    except ValidationError as error:
        print(f"sra: invalid configuration: {error}", file=sys.stderr)
        return 2
    if args.command == "serve":
        return _serve(args, settings)
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
                args.question,
                model=model,
                toolbox=toolbox,
                limits=limits,
                observe=observe,
                prompt_version=args.prompt or settings.prompt,
            )

    try:
        run = asyncio.run(ask())
    except (ToolsUnavailable, ModelUnavailable) as error:
        print(f"sra: {error}", file=sys.stderr)
        return 1

    if settings.audit_database_url is not None:
        audit = PostgresAudit(settings.audit_database_url.get_secret_value())
        print(f"sra · audited as {audit.record(run, user='cli')}", file=sys.stderr)
    if args.json:
        print(run.model_dump_json(indent=2))
    elif run.diagnosis is not None:
        print(render(run.diagnosis, run.outcome))
    return 0 if run.outcome == "answered" else 3
