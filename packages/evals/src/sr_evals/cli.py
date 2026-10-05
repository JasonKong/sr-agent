"""`sr-evals`: build the frozen dataset, and run the agent against it (DESIGN.md 7.5).

`sr-evals run` needs the default world seeded (`make seed`) and the MCP server running over
HTTP (`make mcp-http`). It writes a JSON and a Markdown report, compared with the previous
report for the same cases, and each case's full run (transcript included) under `var/evals/`.
`--dry-run` uses an oracle that reads the answer key: the whole pipeline at no cost.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from sr_agent.anthropic_model import model_from_settings
from sr_agent.api import create_app
from sr_agent.audit import MemoryAudit
from sr_agent.loop import Limits
from sr_agent.model import Effort, ModelClient, ModelUnavailable
from sr_agent.prompts import PROMPT_VERSIONS
from sr_agent.settings import AgentSettings
from sr_agent.toolbox import (
    MINIMAL_DESCRIPTIONS,
    Toolbox,
    ToolsUnavailable,
    connect_http,
    with_descriptions,
    without,
)
from sr_db.domain import FailureCode
from sr_evals.dataset import (
    DATASET_V1,
    Dataset,
    EvalCase,
    build_dataset,
    load_dataset,
    world_for,
    write_dataset,
)
from sr_evals.experiments import Config, render_experiments, run_experiments
from sr_evals.oracle import OracleModel, QuestionOracle
from sr_evals.report import REPORTS_DIR, EvalReport, compare, find_previous, summarise, write_report
from sr_evals.retrieval import load_queries
from sr_evals.runner import CaseResult, run_cases
from sr_evals.scorers import WorldIndex, score_case
from sr_mcp.paths import project_root
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate

Connect = Callable[[str, str], AbstractAsyncContextManager[Toolbox]]

# One clean case per code: the CI smoke set.
SMOKE = [f"{code.value[:3]}-1" for code in FailureCode]
# Tools that come with RAG (M4); `--no-rag` hides them.
RAG_TOOLS = {"search_knowledge", "get_runbook"}


def select_cases(dataset: Dataset, which: str) -> list[EvalCase]:
    if which == "all":
        return list(dataset.cases)
    wanted = SMOKE if which == "smoke" else [w.strip() for w in which.split(",") if w.strip()]
    by_id = {c.case_id: c for c in dataset.cases}
    unknown = [w for w in wanted if w not in by_id]
    if unknown:
        raise ValueError(f"unknown case ids: {', '.join(unknown)}")
    return [by_id[w] for w in wanted]


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=project_root(),
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _build(args: argparse.Namespace) -> int:
    config = SimConfig()
    dataset = build_dataset(generate(config), config)
    write_dataset(dataset, args.out)
    references = Counter(c.reference for c in dataset.cases)
    print(f"{len(dataset.cases)} cases written to {args.out}")
    print("references: " + ", ".join(f"{k}={n}" for k, n in sorted(references.items())))
    return 0


def _run(args: argparse.Namespace, connect: Connect) -> int:
    dataset = load_dataset(args.dataset)
    try:
        cases = select_cases(dataset, args.cases)
    except ValueError as error:
        print(f"sr-evals: {error}", file=sys.stderr)
        return 2
    if not _seeded_world_matches(dataset, args.labels):
        return 2
    settings = AgentSettings()
    if settings.mcp_token is None:
        print(
            "sr-evals: set MCP_HTTP_TOKEN (the MCP server's bearer token) in .env", file=sys.stderr
        )
        return 2

    world = world_for(dataset)
    index = WorldIndex.of(world)
    clinic_of = {r.id: r.recipient_clinic_ref for r in world.referrals}
    if args.dry_run:
        model_name = OracleModel.name
        effort_label: str = args.effort or settings.effort
        fallback, thinking = False, "n/a"

        def model_for(case: EvalCase) -> ModelClient:
            return OracleModel(case, clinic_of[case.composer_id])

    else:
        try:
            shared = model_from_settings(
                settings,
                model=args.model,
                effort=args.effort,
                timeout_s=args.time_limit,
                thinking_display=args.thinking_display,
            )
        except ModelUnavailable as error:
            print(f"sr-evals: {error}", file=sys.stderr)
            return 2
        model_name = shared.name
        effort_label = shared.effort or "n/a"
        fallback, thinking = shared.fallback, shared.thinking

        def model_for(case: EvalCase) -> ModelClient:
            return shared

    limits = Limits(
        max_tool_calls=args.max_tool_calls or settings.max_tool_calls,
        time_limit_s=args.time_limit or settings.time_limit_s,
        token_budget=settings.token_budget,
    )
    started = time.perf_counter()

    def progress(result: CaseResult) -> None:
        s = score_case(result.case, result.run, index, error=result.error)
        mark = "ok   " if s.root_cause_correct else "WRONG"
        said = s.predicted.value if s.predicted else (s.error or s.outcome)
        print(
            f"{s.case_id:<28} {mark} {said} ({s.duration_ms / 1000:.1f} s, {s.tool_calls} tools)",
            file=sys.stderr,
            flush=True,
        )

    prompt = args.prompt or settings.prompt
    url = args.mcp_url or settings.mcp_url
    token = settings.mcp_token.get_secret_value()

    async def evaluate() -> list[CaseResult]:
        async with connect(url, token) as toolbox:
            tools = without(toolbox, RAG_TOOLS) if args.no_rag else toolbox
            if args.tool_descriptions == "v2":
                tools = with_descriptions(tools, MINIMAL_DESCRIPTIONS)
            return await run_cases(
                cases,
                model_for=model_for,
                toolbox=tools,
                limits=limits,
                concurrency=args.concurrency,
                on_case=progress,
                prompt_version=prompt,
                max_cost_usd=args.max_cost_usd,
            )

    try:
        results = asyncio.run(evaluate())
    except ToolsUnavailable as error:
        print(f"sr-evals: {error}", file=sys.stderr)
        return 1

    scores = [score_case(r.case, r.run, index, error=r.error) for r in results]
    now = datetime.now(UTC)
    report_id = f"{now:%Y%m%d-%H%M%S}-{model_name}" + (f"-{args.label}" if args.label else "")
    reports_dir = args.reports_dir or (
        project_root() / "var" / "evals" / "reports" if args.dry_run else REPORTS_DIR
    )
    report = EvalReport(
        report_id=report_id,
        created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        dataset_version=dataset.version,
        case_set=args.cases,
        model=model_name,
        effort=effort_label,
        prompt_version=prompt,
        flags={
            "dry_run": args.dry_run,
            "no_rag": args.no_rag,
            "tool_descriptions": args.tool_descriptions,
            "max_tool_calls": limits.max_tool_calls,
            "time_limit_s": limits.time_limit_s,
            "concurrency": args.concurrency,
            "fallback": fallback,
            "thinking": thinking,
            "max_cost_usd": args.max_cost_usd,
        },
        git_commit=_git_commit(),
        duration_s=round(time.perf_counter() - started, 1),
        metrics=summarise(scores, model=model_name),
        scores=scores,
        comparison=None,
    )
    previous = find_previous(
        reports_dir, case_set=args.cases, dataset_version=dataset.version, before=report_id
    )
    if previous is not None:
        report = report.model_copy(update={"comparison": compare(report, previous)})
    _, md_path = write_report(report, reports_dir)

    runs_dir = (args.runs_dir or project_root() / "var" / "evals" / "runs") / report_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        body = result.run.model_dump(mode="json") if result.run else {"error": result.error}
        (runs_dir / f"{result.case.case_id}.json").write_text(json.dumps(body, indent=2) + "\n")

    m = report.metrics
    cost = f", ${m.cost_total_usd:.2f} total" if m.cost_total_usd is not None else ""
    print(
        f"accuracy {m.accuracy * 100:.1f}% over {m.cases} cases, p50 {m.latency_p50_s:.1f} s{cost}"
    )
    print(f"report: {md_path}")
    return 0


def _seeded_world_matches(dataset: Dataset, labels: Path) -> bool:
    if not labels.exists():
        print(
            f"sr-evals: {labels} not found: seed the default world with `make seed`",
            file=sys.stderr,
        )
        return False
    if json.loads(labels.read_text()).get("config") != dataset.world:
        print(
            "sr-evals: the seeded world is not the dataset's world: re-seed with `make seed`",
            file=sys.stderr,
        )
        return False
    return True


def _serve_oracle(args: argparse.Namespace) -> int:
    import uvicorn

    dataset = load_dataset(args.dataset)
    if not _seeded_world_matches(dataset, args.labels):
        return 2
    settings = AgentSettings()
    if settings.mcp_token is None:
        print(
            "sr-evals: set MCP_HTTP_TOKEN (the MCP server's bearer token) in .env",
            file=sys.stderr,
        )
        return 2
    oracle = QuestionOracle(world_for(dataset))
    app = create_app(
        model_for=lambda: oracle,
        connect=connect_http,
        audit=MemoryAudit(),
        mcp_url=settings.mcp_url,
        mcp_token=settings.mcp_token.get_secret_value(),
        api_token=settings.api_token.get_secret_value() if settings.api_token else None,
    )
    print(
        f"sr-evals: the agent API with an oracle model on http://{args.host}:{args.port}",
        file=sys.stderr,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def _retrieval(args: argparse.Namespace) -> int:
    configs = [
        Config(embedder=e.strip(), chunk_chars=size)
        for e in args.embedders.split(",")
        for size in (int(c) for c in args.chunk_sizes.split(","))
    ]
    started = time.perf_counter()
    rows = run_experiments(load_queries(), configs)
    table = render_experiments(rows)
    now = datetime.now(UTC)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.out_dir / f"{now:%Y%m%d-%H%M%S}-retrieval"
    stem.with_suffix(".json").write_text(
        json.dumps([r.model_dump(mode="json") for r in rows], indent=2) + "\n"
    )
    header = (
        f"# Retrieval experiments {now:%Y-%m-%d %H:%M} UTC\n\n"
        f"40 labelled queries (`retrieval_v1.json`), default world, commit `{_git_commit()}`, "
        f"{time.perf_counter() - started:.0f} s. The code filter uses each query's labelled code "
        "(an upper bound).\n\n"
    )
    stem.with_suffix(".md").write_text(header + table)
    print(table)
    print(f"report: {stem.with_suffix('.md')}")
    return 0


def main(argv: list[str] | None = None, *, connect: Connect = connect_http) -> int:
    parser = argparse.ArgumentParser(prog="sr-evals", description="SR-Agent evaluation.")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="rebuild the frozen dataset from the default world")
    build.add_argument("--out", type=Path, default=DATASET_V1)

    run = commands.add_parser("run", help="run the agent over the dataset and write a report")
    run.add_argument("--cases", default="all", help="all, smoke, or case ids separated by commas")
    run.add_argument("--dry-run", action="store_true", help="an oracle that reads the answer key")
    run.add_argument("--model", help="model id (default SRA_MODEL or claude-opus-5-5)")
    run.add_argument("--effort", choices=get_args(Effort), help="default SRA_EFFORT or medium")
    run.add_argument(
        "--thinking-display",
        choices=["omitted", "summarized"],
        help="default SRA_THINKING_DISPLAY or omitted; summarized thinking costs the same",
    )
    run.add_argument(
        "--max-cost-usd",
        type=float,
        help="stop starting cases once the finished ones cost this much (estimated, list prices)",
    )
    run.add_argument("--no-rag", action="store_true", help="hide the knowledge tools (ablation)")
    run.add_argument("--prompt", choices=sorted(PROMPT_VERSIONS), help="default SRA_PROMPT or v1")
    run.add_argument(
        "--tool-descriptions",
        choices=["v1", "v2"],
        default="v1",
        help="v1: the server's descriptions; v2: one-line descriptions (ablation)",
    )
    run.add_argument("--concurrency", type=int, default=4)
    run.add_argument("--max-tool-calls", type=int)
    run.add_argument("--time-limit", type=float, help="seconds per case")
    run.add_argument("--label", help="a short tag for the report id")
    run.add_argument("--mcp-url")
    run.add_argument("--dataset", type=Path, default=DATASET_V1)
    run.add_argument("--labels", type=Path, default=project_root() / "var" / "sim" / "labels.json")
    run.add_argument("--reports-dir", type=Path)
    run.add_argument("--runs-dir", type=Path)

    retrieval = commands.add_parser(
        "retrieval", help="retrieval experiments on scratch databases (T4.4)"
    )
    retrieval.add_argument("--embedders", default="bge-small,minilm")
    retrieval.add_argument("--chunk-sizes", default="2000,400")
    retrieval.add_argument("--out-dir", type=Path, default=REPORTS_DIR / "retrieval")

    serve = commands.add_parser(
        "serve-oracle", help="the agent's HTTP API with an oracle model (for the console and e2e)"
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8766)
    serve.add_argument("--dataset", type=Path, default=DATASET_V1)
    serve.add_argument(
        "--labels", type=Path, default=project_root() / "var" / "sim" / "labels.json"
    )

    args = parser.parse_args(argv)
    if args.command == "serve-oracle":
        return _serve_oracle(args)
    if args.command == "build":
        return _build(args)
    if args.command == "retrieval":
        return _retrieval(args)
    return _run(args, connect)
