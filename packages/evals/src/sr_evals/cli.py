"""`sr-evals`: build the frozen evaluation dataset, and run the agent against it."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from sr_evals.dataset import DATASET_V1, build_dataset, write_dataset
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate


def _build(args: argparse.Namespace) -> int:
    config = SimConfig()
    dataset = build_dataset(generate(config), config)
    write_dataset(dataset, args.out)
    references = Counter(c.reference for c in dataset.cases)
    print(f"{len(dataset.cases)} cases written to {args.out}")
    print("references: " + ", ".join(f"{k}={n}" for k, n in sorted(references.items())))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sr-evals", description="SR-Agent evaluation.")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="rebuild the frozen dataset from the default world")
    build.add_argument("--out", type=Path, default=DATASET_V1)
    args = parser.parse_args(argv)
    return _build(args)
