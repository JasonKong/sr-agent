"""`sr-mcp`: run the MCP server (stdio for Claude Desktop and Claude Code)."""

from __future__ import annotations

import argparse

from sr_mcp.adapters.null import NoKnowledge, NoLogs
from sr_mcp.adapters.postgres import PostgresReferralStore
from sr_mcp.deps import Deps
from sr_mcp.server import create_server
from sr_mcp.settings import McpSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sr-mcp", description="SR-Agent MCP server.")
    parser.add_argument("--transport", choices=["stdio"], default="stdio")
    args = parser.parse_args(argv)

    settings = McpSettings()  # values come from the environment
    store = PostgresReferralStore(
        str(settings.database_url), mask_salt=settings.mask_salt.get_secret_value()
    )
    deps = Deps(
        store=store,
        logs=NoLogs(),
        knowledge=NoKnowledge(),
        max_result_chars=settings.max_result_chars,
        caller=args.transport,
    )
    try:
        create_server(deps).run(args.transport)
    finally:
        store.close()
    return 0
