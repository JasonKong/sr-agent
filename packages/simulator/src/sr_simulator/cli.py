"""`sim` command line: generate simulated data and load it into Postgres."""

import argparse
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import psycopg

from sr_db.domain import FailureCode
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


def _write_labels(path: Path, config: SimConfig, labels: dict[str, FailureCode]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "seed": config.seed,
        "anchor_time": config.anchor_time.isoformat(),
        "failure_rate": config.failure_rate,
        "note": "Referrals not listed are F00_NO_FAULT.",
        "labels": {referral_id: code.value for referral_id, code in sorted(labels.items())},
    }
    path.write_text(json.dumps(document, indent=2) + "\n")


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
    seed.add_argument(
        "--failure-rate",
        type=float,
        default=defaults.failure_rate,
        help="share of referrals given an injected failure (default %(default)s)",
    )
    seed.add_argument(
        "--labels",
        type=Path,
        default=Path("var/sim/labels.json"),
        help="where to write the ground-truth labels; never stored in the database",
    )
    args = parser.parse_args(argv)

    try:
        config = SimConfig(
            seed=args.seed,
            anchor_time=args.anchor_time,
            practices=args.practices,
            clinics=args.clinics,
            referrals=args.referrals,
            history_days=args.history_days,
            failure_rate=args.failure_rate,
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
    _write_labels(args.labels, config, world.labels)
    codes = Counter(world.labels.values())
    codes[FailureCode.F00_NO_FAULT] = config.referrals - len(world.labels)
    print("cases: " + ", ".join(f"{c.value[:3]}={codes[c]}" for c in FailureCode))
    print(f"labels written to {args.labels}")
    print(f"seeded seed={config.seed} anchor={config.anchor_time.isoformat()} in {elapsed:.1f}s")
    return 0
