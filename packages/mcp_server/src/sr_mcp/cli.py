"""`sr-mcp`: run the MCP server over stdio (Claude Desktop, Claude Code) or HTTP (the agent)."""

from __future__ import annotations

import argparse
import sys

import uvicorn

from sr_mcp.adapters.jsonl_logs import JsonlLogSource
from sr_mcp.adapters.postgres import PostgresReferralStore
from sr_mcp.adapters.postgres_knowledge import PostgresKnowledge
from sr_mcp.deps import Deps
from sr_mcp.http import MIN_TOKEN_LENGTH, build_app
from sr_mcp.knowledge.embedder import make_embedder
from sr_mcp.paths import project_root, resolve
from sr_mcp.server import create_server
from sr_mcp.settings import McpSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sr-mcp", description="SR-Agent MCP server.")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP only (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP only (default 8765)")
    args = parser.parse_args(argv)

    # Resolve .env and relative paths against the project, not the working directory.
    root = project_root()
    settings = McpSettings(_env_file=root / ".env")
    token = settings.http_token.get_secret_value() if settings.http_token else ""
    if args.transport == "http" and len(token) < MIN_TOKEN_LENGTH:
        print(
            f"sr-mcp: HTTP needs MCP_HTTP_TOKEN of at least {MIN_TOKEN_LENGTH} characters, e.g."
            ' python -c "import secrets; print(secrets.token_urlsafe(32))"',
            file=sys.stderr,
        )
        raise SystemExit(2)

    url = str(settings.database_url)
    store = PostgresReferralStore(url, mask_salt=settings.mask_salt.get_secret_value())
    embedder = make_embedder(settings.embedder, cache_dir=resolve(settings.model_dir, root))
    knowledge = PostgresKnowledge(url, embedder=embedder)
    deps = Deps(
        store=store,
        logs=JsonlLogSource(resolve(settings.log_dir, root)),
        knowledge=knowledge,
        max_result_chars=settings.max_result_chars,
        caller=args.transport,
    )
    server = create_server(deps)
    try:
        if args.transport == "http":
            app = build_app(server, token, host=args.host)
            uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
        else:
            server.run("stdio")
    finally:
        store.close()
        knowledge.close()
    return 0
