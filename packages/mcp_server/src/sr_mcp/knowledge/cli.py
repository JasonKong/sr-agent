"""`sr-ingest`: chunk, mask, embed and store past tickets and runbooks (DESIGN.md 7.3)."""

from __future__ import annotations

import argparse
import time

import psycopg

from sr_db.settings import DatabaseSettings
from sr_mcp.knowledge.embedder import EMBEDDERS, make_embedder
from sr_mcp.knowledge.ingest import ingest
from sr_mcp.paths import project_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sr-ingest", description=__doc__)
    parser.add_argument("--embedder", choices=sorted(EMBEDDERS), default="bge-small")
    parser.add_argument(
        "--chunk-chars", type=int, default=2000, help="runbook chunk size (default 2000)"
    )
    args = parser.parse_args(argv)

    root = project_root()
    settings = DatabaseSettings(_env_file=root / ".env")
    embedder = make_embedder(args.embedder, cache_dir=root / "var" / "models")
    started = time.perf_counter()
    with psycopg.connect(settings.url, autocommit=True) as conn:
        counts = ingest(conn, embedder, max_chars=args.chunk_chars)
    elapsed = time.perf_counter() - started
    print(
        f"{embedder.name}: {counts.inserted} inserted, {counts.updated} updated, "
        f"{counts.unchanged} unchanged, {counts.deleted} deleted in {elapsed:.1f}s"
    )
    return 0
