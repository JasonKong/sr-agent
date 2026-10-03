"""`sr-mcp`: run the MCP server (stdio for Claude Desktop and Claude Code)."""

from __future__ import annotations

import argparse

from sr_mcp.adapters.jsonl_logs import JsonlLogSource
from sr_mcp.adapters.postgres import PostgresReferralStore
from sr_mcp.adapters.postgres_knowledge import PostgresKnowledge
from sr_mcp.deps import Deps
from sr_mcp.paths import project_root, resolve
from sr_mcp.server import create_server
from sr_mcp.settings import McpSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sr-mcp", description="SR-Agent MCP server.")
    parser.add_argument("--transport", choices=["stdio"], default="stdio")
    args = parser.parse_args(argv)

    # Resolve .env and relative paths against the project, not the working directory.
    root = project_root()
    settings = McpSettings(_env_file=root / ".env")
    url = str(settings.database_url)
    store = PostgresReferralStore(url, mask_salt=settings.mask_salt.get_secret_value())
    knowledge = PostgresKnowledge(url)
    deps = Deps(
        store=store,
        logs=JsonlLogSource(resolve(settings.log_dir, root)),
        knowledge=knowledge,
        max_result_chars=settings.max_result_chars,
        caller=args.transport,
    )
    try:
        create_server(deps).run(args.transport)
    finally:
        store.close()
        knowledge.close()
    return 0
