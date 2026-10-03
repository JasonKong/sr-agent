"""`sim` command line: generate simulated data and load it into Postgres."""

import argparse
import time
from datetime import datetime

import psycopg

from sr_db.settings import DatabaseSettings
from sr_simulator.config import DEFAULT_ANCHOR, SimConfig
from sr_simulator.generate import generate
from sr_simulator.write import write_world


def _anchor_time(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 time: {text}") from error
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError("anchor time must include a timezone, e.g. +13:00")
    return moment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sim", description="SR-Agent data simulator.")
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed", help="replace all simulator data with a generated world")
    defaults = SimConfig()
    seed.add_argument("--seed", type=int, default=defaults.seed)
    seed.add_argument(
        "--anchor-time",
        type=_anchor_time,
        default=DEFAULT_ANCHOR,
        help="'now' in the simulated world: ISO 8601 with an offset"
        f" (default {DEFAULT_ANCHOR.isoformat()})",
    )
    seed.add_argument("--practices", type=int, default=defaults.practices)
    seed.add_argument("--clinics", type=int, default=defaults.clinics)
    seed.add_argument("--referrals", type=int, default=defaults.referrals)
    seed.add_argument("--history-days", type=int, default=defaults.history_days)
    args = parser.parse_args(argv)

    try:
        config = SimConfig(
            seed=args.seed,
            anchor_time=args.anchor_time,
            practices=args.practices,
            clinics=args.clinics,
            referrals=args.referrals,
            history_days=args.history_days,
        )
    except ValueError as error:
        parser.error(str(error))

    started = time.perf_counter()
    world = generate(config)
    with psycopg.connect(DatabaseSettings().url, autocommit=True) as conn:
        counts = write_world(conn, world)
    elapsed = time.perf_counter() - started

    width = max(map(len, counts))
    for table, count in counts.items():
        print(f"{table:<{width}}  {count:>7}")
    print(f"seeded seed={config.seed} anchor={config.anchor_time.isoformat()} in {elapsed:.1f}s")
    return 0
