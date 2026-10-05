"""`sr-evals`: build the frozen datasets, run the agent against them, compare runs (DESIGN.md 7.5).

`sr-evals run` needs the dataset's world seeded (`make seed`) and the MCP server running
over HTTP (`make mcp-http`). It writes a JSON and a Markdown report, compared with the
previous report for the same cases (or `--compare-to` a named one), and each case's full
run (transcript included) under `var/evals/`. `--dry-run` uses an oracle that reads the
answer key: the whole pipeline at no cost.

`sr-evals compare A B` sets two reports side by side with the sign-test verdict, and
`sr-evals rescore R` scores a run's stored answers again, with no model and no database.
`sr-evals grade R` grades R's answers with the model-graded rubric, and `sr-evals
calibrate` measures a grader against answers whose label is known.
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
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from sr_agent.anthropic_model import AnthropicModel, model_from_settings
from sr_agent.api import create_app
from sr_agent.audit import MemoryAudit
from sr_agent.loop import AgentRun, Limits
from sr_agent.model import Effort, ModelClient, ModelUnavailable
from sr_agent.model_profiles import profile_for
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
    DATASET_SEEDS,
    DATASET_V1,
    DATASETS,
    Dataset,
    EvalCase,
    build_dataset,
    load_dataset,
    world_for,
    write_dataset,
)
from sr_evals.experiments import Config, render_experiments, run_experiments
from sr_evals.grader import (
    GRADER_VERSION,
    CalibrationReport,
    Grade,
    GradeItem,
    OracleGrader,
    RubricReport,
    calibrate,
    grade_by_batch,
    grade_items,
    render_calibration,
    render_rubric,
    rubric_sha,
    summarise_rubric,
)
from sr_evals.grader_items import (
    CALIBRATION_V1,
    HAND_LABELS_V1,
    calibration_items,
    labelling_sample,
    load_calibration,
    report_items,
    write_calibration,
    write_labelling_template,
)
from sr_evals.inspect import RunCheck, check_run, render_checks
from sr_evals.oracle import OracleModel, QuestionOracle
from sr_evals.report import (
    REPORTS_DIR,
    EvalReport,
    compare,
    find_previous,
    render_comparison,
    summarise,
    write_report,
)
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


def dataset_path(value: str) -> Path:
    """A dataset by version (`v1`, `holdout_v1`) or by path."""
    return DATASETS.get(value) or Path(value)


def _report_dirs(args: argparse.Namespace) -> list[Path]:
    given = [args.reports_dir] if args.reports_dir else []
    return [*given, REPORTS_DIR, project_root() / "var" / "evals" / "reports"]


def load_report(ref: str, dirs: list[Path]) -> tuple[EvalReport, Path]:
    """A report by id (looked up in `dirs`) or by path, and the file it came from."""
    for path in [Path(ref), *(d / f"{ref}.json" for d in dirs)]:
        if path.suffix == ".json" and path.is_file():
            return EvalReport.model_validate_json(path.read_text()), path
    raise FileNotFoundError(f"no report {ref!r} in {', '.join(str(d) for d in dirs)}")


def _build(args: argparse.Namespace) -> int:
    config = replace(SimConfig(), seed=DATASET_SEEDS[args.set])
    dataset = build_dataset(generate(config), config, version=args.set)
    out = args.out or DATASETS[args.set]
    write_dataset(dataset, out)
    references = Counter(c.reference for c in dataset.cases)
    print(f"{len(dataset.cases)} cases written to {out} (seed {config.seed})")
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

    reports_dir = args.reports_dir or (
        project_root() / "var" / "evals" / "reports" if args.dry_run else REPORTS_DIR
    )
    base: EvalReport | None = None
    if args.compare_to:
        try:
            base, _ = load_report(args.compare_to, _report_dirs(args))
        except FileNotFoundError as error:
            print(f"sr-evals: --compare-to: {error}", file=sys.stderr)
            return 2
        if base.dataset_version != dataset.version:
            print(
                f"sr-evals: --compare-to {args.compare_to} is on dataset "
                f"{base.dataset_version}, not {dataset.version}",
                file=sys.stderr,
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

    now = datetime.now(UTC)
    report_id = f"{now:%Y%m%d-%H%M%S}-{model_name}" + (f"-{args.label}" if args.label else "")
    # The runs first: they are what the run cost, and the report can be rebuilt from them.
    runs_dir = (args.runs_dir or project_root() / "var" / "evals" / "runs") / report_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        body = result.run.model_dump(mode="json") if result.run else {"error": result.error}
        (runs_dir / f"{result.case.case_id}.json").write_text(json.dumps(body, indent=2) + "\n")

    scores = [score_case(r.case, r.run, index, error=r.error) for r in results]
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
    previous = base or find_previous(
        reports_dir, case_set=args.cases, dataset_version=dataset.version, before=report_id
    )
    if previous is not None:
        report = report.model_copy(update={"comparison": compare(report, previous)})
    _, md_path = write_report(report, reports_dir)

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


def _compare(args: argparse.Namespace) -> int:
    dirs = _report_dirs(args)
    try:
        older, _ = load_report(args.older, dirs)
        newer, _ = load_report(args.newer, dirs)
    except FileNotFoundError as error:
        print(f"sr-evals: {error}", file=sys.stderr)
        return 2
    if older.dataset_version != newer.dataset_version:
        print(
            f"sr-evals: the runs are on different datasets ({older.dataset_version}, "
            f"{newer.dataset_version}): their cases are not the same",
            file=sys.stderr,
        )
        return 2
    print(render_comparison(newer, older))
    return 0


def _rescore(args: argparse.Namespace) -> int:
    """Score a run's stored answers again, with today's scorers; compared with the original."""
    try:
        original, path = load_report(args.report, _report_dirs(args))
    except FileNotFoundError as error:
        print(f"sr-evals: {error}", file=sys.stderr)
        return 2
    if original.dataset_version not in DATASETS:
        print(f"sr-evals: unknown dataset {original.dataset_version!r}", file=sys.stderr)
        return 2
    runs = (args.runs_dir or project_root() / "var" / "evals" / "runs") / original.report_id
    missing = [s.case_id for s in original.scores if not (runs / f"{s.case_id}.json").is_file()]
    if missing:
        print(f"sr-evals: no stored run in {runs} for {', '.join(missing)}", file=sys.stderr)
        return 2
    dataset = load_dataset(DATASETS[original.dataset_version])
    by_id = {c.case_id: c for c in dataset.cases}
    index = WorldIndex.of(world_for(dataset))
    scores = []
    for old in original.scores:
        body = json.loads((runs / f"{old.case_id}.json").read_text())
        if set(body) == {"error"}:
            scores.append(score_case(by_id[old.case_id], None, index, error=body["error"]))
        else:
            scores.append(score_case(by_id[old.case_id], AgentRun.model_validate(body), index))
    rescored = original.model_copy(
        update={
            "report_id": f"{original.report_id}-rescored",
            "flags": original.flags
            | {
                "rescored_from": original.report_id,
                "rescored_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "rescored_commit": _git_commit(),
            },
            "metrics": summarise(scores, model=original.model),
            "scores": scores,
        }
    )
    rescored = rescored.model_copy(update={"comparison": compare(rescored, original)})
    _, md_path = write_report(rescored, path.parent)
    print(
        f"accuracy {original.metrics.accuracy * 100:.1f}% -> "
        f"{rescored.metrics.accuracy * 100:.1f}% over {rescored.metrics.cases} cases"
    )
    print(f"report: {md_path}")
    return 0


DEFAULT_GRADER = "claude-haiku-4-5"


def grader_model(name: str, settings: AgentSettings) -> AnthropicModel:
    """A grader at its cheapest: low effort where it has effort levels, no thinking budget.

    No refusal fallback, so a direct request and a batch request are the same request.
    """
    profile = profile_for(name)
    key = settings.anthropic_api_key
    return AnthropicModel(
        model=name,
        effort="low" if profile.effort else None,
        fallback=False,
        thinking=profile.thinking == "adaptive",
        api_key=key.get_secret_value() if key else None,
        timeout_s=60.0,
    )


def _rubric_dir(args: argparse.Namespace) -> Path:
    if args.out_dir:
        out: Path = args.out_dir
    elif args.dry_run:
        out = project_root() / "var" / "evals" / "rubric"
    else:
        out = REPORTS_DIR / "rubric"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _grade_all(
    items: list[GradeItem], args: argparse.Namespace, settings: AgentSettings
) -> list[Grade]:
    if args.dry_run:
        return asyncio.run(grade_items(items, model_for=OracleGrader))
    model = grader_model(args.grader_model, settings)
    if getattr(args, "batch", False):
        return asyncio.run(grade_by_batch(items, model))
    return asyncio.run(grade_items(items, model_for=lambda item: model))


def _sample_for_labelling(args: argparse.Namespace) -> int:
    """A blind template of real answers to label by hand (TE8), never overwriting labels."""
    if args.labels.exists():
        print(f"sr-evals: {args.labels} exists; it may hold hand labels", file=sys.stderr)
        return 2
    try:
        graded, _ = load_report(args.sample, _report_dirs(args))
    except FileNotFoundError as error:
        print(f"sr-evals: {error}", file=sys.stderr)
        return 2
    runs = (args.runs_dir or project_root() / "var" / "evals" / "runs") / graded.report_id
    dataset = load_dataset(DATASETS[graded.dataset_version])
    case_ids = [s.case_id for s in graded.scores]
    items, _ = report_items(case_ids, runs, dataset, report_id=graded.report_id)
    correct = {s.case_id: s.root_cause_correct for s in graded.scores}
    sample = labelling_sample(items, correct, args.n)
    write_labelling_template(sample, args.labels)
    print(f"{len(sample)} answers to label written to {args.labels}")
    return 0


def _calibrate(args: argparse.Namespace) -> int:
    if args.sample:
        return _sample_for_labelling(args)
    if args.build:
        items = calibration_items(load_dataset(DATASET_V1))
        write_calibration(items, args.calibration)
        print(f"{len(items)} calibration items written to {args.calibration}")
        return 0
    items = [i for i in load_calibration(args.calibration, args.labels) if i.label is not None]
    settings = AgentSettings()
    try:
        repeats = [_grade_all(items, args, settings) for _ in range(args.repeat)]
    except ModelUnavailable as error:
        print(f"sr-evals: {error}", file=sys.stderr)
        return 2 if "profile" in str(error) or "credentials" in str(error) else 1
    name = "oracle" if args.dry_run else args.grader_model
    now = datetime.now(UTC)
    report = CalibrationReport(
        created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        git_commit=_git_commit(),
        items=len(items),
        calibration=calibrate(items, repeats, grader_model=name),
    )
    stem = _rubric_dir(args) / f"calibration-{now:%Y%m%d-%H%M%S}-{name}-{GRADER_VERSION}"
    stem.with_suffix(".json").write_text(report.model_dump_json(indent=2) + "\n")
    stem.with_suffix(".md").write_text(render_calibration(report))
    for criterion, rate in report.calibration.agreement.items():
        print(f"{criterion} {(rate.rate or 0) * 100:.1f}% agreement over {rate.n} verdicts")
    print(f"report: {stem.with_suffix('.md')}")
    return 0


def _grade(args: argparse.Namespace) -> int:
    try:
        graded, _ = load_report(args.report, _report_dirs(args))
    except FileNotFoundError as error:
        print(f"sr-evals: {error}", file=sys.stderr)
        return 2
    runs = (args.runs_dir or project_root() / "var" / "evals" / "runs") / graded.report_id
    if not runs.is_dir() or graded.dataset_version not in DATASETS:
        print(f"sr-evals: no stored runs in {runs} for this dataset", file=sys.stderr)
        return 2
    dataset = load_dataset(DATASETS[graded.dataset_version])
    case_ids = [s.case_id for s in graded.scores]
    items, no_answer = report_items(case_ids, runs, dataset, report_id=graded.report_id)
    settings = AgentSettings()
    try:
        grades = _grade_all(items, args, settings)
    except ModelUnavailable as error:
        print(f"sr-evals: {error}", file=sys.stderr)
        return 2 if "profile" in str(error) or "credentials" in str(error) else 1
    summary = summarise_rubric(
        grades,
        root_cause_correct={s.case_id: s.root_cause_correct for s in graded.scores},
        code_of={s.case_id: s.code.value[:3] for s in graded.scores},
    )
    report = RubricReport(
        graded_report=graded.report_id,
        dataset_version=graded.dataset_version,
        grader_version=GRADER_VERSION,
        rubric_sha=rubric_sha(),
        grader_model="oracle" if args.dry_run else args.grader_model,
        batch=bool(args.batch) and not args.dry_run,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        git_commit=_git_commit(),
        no_answer=no_answer,
        summary=summary,
        grades=grades,
    )
    stem = _rubric_dir(args) / f"{graded.report_id}-{GRADER_VERSION}"
    stem.with_suffix(".json").write_text(report.model_dump_json(indent=2) + "\n")
    stem.with_suffix(".md").write_text(render_rubric(report))
    for criterion, rate in summary.pass_rates.items():
        print(f"{criterion} {(rate.rate or 0) * 100:.1f}% over {rate.n} answers")
    print(f"report: {stem.with_suffix('.md')}")
    return 0


def _inspect(args: argparse.Namespace) -> int:
    """A row per stored run: a report id's runs, or JSON files from `sra ask --json`."""
    root = args.runs_dir or project_root() / "var" / "evals" / "runs"
    checks: list[RunCheck] = []
    errors: dict[str, str] = {}
    for ref in args.refs:
        given = Path(ref)
        files = [given] if given.is_file() else sorted((root / ref).glob("*.json"))
        if not files:
            print(f"sr-evals: no stored runs for {ref!r} in {root}", file=sys.stderr)
            return 2
        for path in files:
            body = json.loads(path.read_text())
            if set(body) == {"error"}:
                errors[path.stem] = body["error"]
            else:
                checks.append(check_run(path.stem, AgentRun.model_validate(body)))
    print(render_checks(checks, errors), end="")
    return 0


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

    build = commands.add_parser("build", help="rebuild a frozen dataset from its world")
    build.add_argument(
        "--set", choices=sorted(DATASETS), default="v1", help="v1 (development) or holdout_v1"
    )
    build.add_argument("--out", type=Path, help="default packages/evals/datasets/<set>.json")

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
    run.add_argument(
        "--dataset", type=dataset_path, default=DATASET_V1, help="v1, holdout_v1 or a path"
    )
    run.add_argument("--compare-to", help="the report id to compare with (default: the latest)")
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
    serve.add_argument("--dataset", type=dataset_path, default=DATASET_V1)
    serve.add_argument(
        "--labels", type=Path, default=project_root() / "var" / "sim" / "labels.json"
    )

    compare_runs = commands.add_parser("compare", help="two reports side by side, with a verdict")
    compare_runs.add_argument("older", help="report id or path")
    compare_runs.add_argument("newer", help="report id or path")
    compare_runs.add_argument("--reports-dir", type=Path)

    rescore = commands.add_parser("rescore", help="score a run's stored answers again")
    rescore.add_argument("report", help="report id or path")
    rescore.add_argument("--reports-dir", type=Path)
    rescore.add_argument("--runs-dir", type=Path)

    grade = commands.add_parser("grade", help="grade a run's answers with the rubric")
    grade.add_argument("report", help="report id or path")
    grade.add_argument("--grader-model", default=DEFAULT_GRADER)
    grade.add_argument("--batch", action="store_true", help="through the Batch API, half price")
    grade.add_argument("--dry-run", action="store_true", help="a rule instead of a model")
    grade.add_argument("--reports-dir", type=Path)
    grade.add_argument("--runs-dir", type=Path)
    grade.add_argument("--out-dir", type=Path, help="default packages/evals/reports/rubric")

    calibration = commands.add_parser("calibrate", help="measure a grader on labelled answers")
    calibration.add_argument("--grader-model", default=DEFAULT_GRADER)
    calibration.add_argument("--repeat", type=int, default=2, help="grade every item N times")
    calibration.add_argument("--dry-run", action="store_true", help="a grader that reads labels")
    calibration.add_argument("--calibration", type=Path, default=CALIBRATION_V1)
    calibration.add_argument("--labels", type=Path, default=HAND_LABELS_V1)
    calibration.add_argument("--build", action="store_true", help="rebuild the synthetic set")
    calibration.add_argument("--sample", help="write a template of this report's answers to label")
    calibration.add_argument("--n", type=int, default=20, help="answers in the template")
    calibration.add_argument("--reports-dir", type=Path)
    calibration.add_argument("--runs-dir", type=Path)
    calibration.add_argument("--out-dir", type=Path, help="default packages/evals/reports/rubric")

    inspect = commands.add_parser("inspect", help="thinking, caching and turns, per stored run")
    inspect.add_argument("refs", nargs="+", help="report ids, or run files from sra ask --json")
    inspect.add_argument("--runs-dir", type=Path)

    args = parser.parse_args(argv)
    if args.command == "inspect":
        return _inspect(args)
    if args.command == "grade":
        return _grade(args)
    if args.command == "calibrate":
        return _calibrate(args)
    if args.command == "compare":
        return _compare(args)
    if args.command == "rescore":
        return _rescore(args)
    if args.command == "serve-oracle":
        return _serve_oracle(args)
    if args.command == "build":
        return _build(args)
    if args.command == "retrieval":
        return _retrieval(args)
    return _run(args, connect)
