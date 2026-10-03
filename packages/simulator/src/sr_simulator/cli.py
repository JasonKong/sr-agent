"""`sim` command line: seed simulated data, and pick labelled cases for evaluation."""

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import psycopg

from sr_db.domain import FailureCode
from sr_db.settings import DatabaseSettings
from sr_simulator.case_rows import case_rows, find_evidence
from sr_simulator.config import DEFAULT_ANCHOR, SimConfig
from sr_simulator.distractors import KINDS
from sr_simulator.generate import generate
from sr_simulator.logs import write_logs
from sr_simulator.scenario import load_distractors, load_scenarios
from sr_simulator.write import write_world

DEFAULT_LABELS = Path("var/sim/labels.json")
DEFAULT_LOGS = Path("var/logs")


def _anchor_time(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 time: {text}") from error
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError("anchor time must include a timezone, e.g. +13:00")
    return moment


def write_labels(
    path: Path,
    config: SimConfig,
    labels: dict[str, FailureCode],
    distractors: dict[str, str] | None = None,
    knowledge: dict[str, str] | None = None,
) -> None:
    """Write the ground truth, with the full config so the same world can be rebuilt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "config": {
            "seed": config.seed,
            "anchor_time": config.anchor_time.isoformat(),
            "practices": config.practices,
            "clinics": config.clinics,
            "referrals": config.referrals,
            "history_days": config.history_days,
            "failure_rate": config.failure_rate,
            "distractor_rate": config.distractor_rate,
            "tickets": config.tickets,
        },
        "note": "Referrals not listed are F00_NO_FAULT. Never load this into the database.",
        "labels": {referral_id: code.value for referral_id, code in sorted(labels.items())},
        "distractors": dict(sorted((distractors or {}).items())),
        "knowledge": dict(sorted((knowledge or {}).items(), key=lambda kv: int(kv[0][4:]))),
    }
    path.write_text(json.dumps(document, indent=2) + "\n")


def _read_config(path: Path) -> SimConfig:
    config = json.loads(path.read_text())["config"]
    config["anchor_time"] = datetime.fromisoformat(config["anchor_time"])
    return SimConfig(**config)


def _seed(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        config = SimConfig(
            seed=args.seed,
            anchor_time=args.anchor_time,
            practices=args.practices,
            clinics=args.clinics,
            referrals=args.referrals,
            history_days=args.history_days,
            tickets=args.tickets,
            failure_rate=args.failure_rate,
            distractor_rate=args.distractor_rate,
        )
    except ValueError as error:
        parser.error(str(error))

    started = time.perf_counter()
    world = generate(config)
    with psycopg.connect(DatabaseSettings().url, autocommit=True) as conn:
        counts = write_world(conn, world)
    log_counts = write_logs(world, args.logs)
    write_labels(args.labels, config, world.labels, world.distractors, world.knowledge_labels)
    elapsed = time.perf_counter() - started

    rows = {**counts, **{f"log {service}": n for service, n in log_counts.items()}}
    width = max(map(len, rows))
    for name, count in rows.items():
        print(f"{name:<{width}}  {count:>7}")
    codes = Counter(world.labels.values())
    codes[FailureCode.F00_NO_FAULT] = config.referrals - len(world.labels)
    print("cases: " + ", ".join(f"{c.value[:3]}={codes[c]}" for c in FailureCode))
    kinds = Counter(world.distractors.values())
    print("distractors: " + ", ".join(f"{k}={n}" for k, n in sorted(kinds.items())))
    tickets = Counter(world.knowledge_labels.values())
    print("knowledge distractors: " + ", ".join(f"{k}={n}" for k, n in sorted(tickets.items())))
    print(f"logs written to {args.logs}/, labels to {args.labels}")
    print(f"seeded seed={config.seed} anchor={config.anchor_time.isoformat()} in {elapsed:.1f}s")
    return 0


def _case(args: argparse.Namespace) -> int:
    """Print one labelled case of a code, with its evidence resolved to record ids."""
    if not args.labels.exists():
        print(f"sim: {args.labels} not found; run `sim seed` first", file=sys.stderr)
        return 1
    world = generate(_read_config(args.labels))
    code: FailureCode = args.code
    kind: str | None = args.distractor
    true_code = {r.id: world.labels.get(r.id, FailureCode.F00_NO_FAULT) for r in world.referrals}
    # Without --distractor, only clean cases: no distractor of any kind.
    candidates = sorted(
        rid for rid, c in true_code.items() if c is code and world.distractors.get(rid) == kind
    )
    if args.pick >= len(candidates):
        what = f"{code.value} cases " + (
            f"with distractor {kind}" if kind else "without distractors"
        )
        message = f"only {len(candidates)} {what} exist; pick 0 to {len(candidates) - 1}"
        print(f"sim: {message}", file=sys.stderr)
        return 1

    referral_id = candidates[args.pick]
    rows = case_rows(world, referral_id)
    spec = load_scenarios()[code]
    distractor = load_distractors()[kind] if kind else None
    items = [(item, "scenario") for item in spec.evidence]
    items += [(item, "distractor") for item in (distractor.evidence if distractor else [])]
    evidence = []
    for item, source in items:
        for row in find_evidence(rows, item):
            record_id = row.id  # type: ignore[attr-defined]
            evidence.append(
                {"table": item.table, "record_id": record_id, "where": item.where, "source": source}
            )
    result = {
        "code": code.value,
        "pick": args.pick,
        "available": len(candidates),
        "composer_id": referral_id,
        "hub_id": rows.referral.hub_referral_id,
        "correlation_id": rows.referral.correlation_id,
        "cs_hears": distractor.cs_hears if distractor else spec.cs_hears,
        "root_cause": spec.root_cause.strip(),
        "recommended_action": spec.recommended_action.strip(),
        "escalate_to_engineering": spec.escalate_to_engineering,
        "distractor": (
            {"kind": kind, "title": distractor.title, "trap": distractor.trap.strip()}
            if distractor
            else None
        ),
        "evidence": evidence,
    }
    print(json.dumps(result, indent=2))
    return 0


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
        "--tickets", type=int, default=defaults.tickets, help="historical support tickets"
    )
    seed.add_argument(
        "--failure-rate",
        type=float,
        default=defaults.failure_rate,
        help="share of referrals given an injected failure (default %(default)s)",
    )
    seed.add_argument(
        "--distractor-rate",
        type=float,
        default=defaults.distractor_rate,
        help="share of referrals given a distractor (default %(default)s)",
    )
    seed.add_argument("--logs", type=Path, default=DEFAULT_LOGS, help="JSONL log directory")
    seed.add_argument(
        "--labels",
        type=Path,
        default=DEFAULT_LABELS,
        help="where to write the ground-truth labels; never stored in the database",
    )

    case = commands.add_parser(
        "case", help="print one labelled case and its expected evidence (for evaluation)"
    )
    case.add_argument("code", type=FailureCode, choices=list(FailureCode), metavar="CODE")
    case.add_argument("--pick", type=int, default=0, help="which case of that code (0-based)")
    case.add_argument(
        "--distractor",
        choices=sorted(KINDS),
        help="pick a case with this distractor (default: cases without any distractor)",
    )
    case.add_argument(
        "--labels", type=Path, default=DEFAULT_LABELS, help="labels file written by `sim seed`"
    )

    args = parser.parse_args(argv)
    if args.command == "seed":
        return _seed(args, parser)
    return _case(args)
