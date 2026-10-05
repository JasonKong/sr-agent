"""Eval reports: metrics, a comparison with the previous run, JSON and Markdown files.

Reports live in `packages/evals/reports/`, one JSON and one Markdown file per run, named by
the run id (a UTC timestamp first, so they sort by time). Each is compared with the latest
earlier report for the same dataset version and case set.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean
from typing import Any

from pydantic import BaseModel, ValidationError

from sr_evals.scorers import CaseScore

REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile: the smallest value with at least q% of values at or below."""
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


class Rate(BaseModel):
    cases: int
    correct: int
    accuracy: float


def _rate(scores: list[CaseScore]) -> Rate:
    correct = sum(s.root_cause_correct for s in scores)
    return Rate(cases=len(scores), correct=correct, accuracy=correct / len(scores))


class Metrics(BaseModel):
    cases: int
    accuracy: float
    accuracy_clean: float | None
    accuracy_distractors: float | None
    per_code: dict[str, Rate]
    per_distractor: dict[str, Rate]
    per_reference: dict[str, Rate]
    schema_valid: float
    evidence_resolves: float
    key_evidence_recall: float
    no_patient_identifiers: float
    escalation_correct: float
    no_clinical_advice: float
    injection_resisted: float | None  # over injection cases only
    outcomes: dict[str, int]
    incomplete: int
    errors: int
    repairs: int
    latency_p50_s: float
    latency_p95_s: float
    tool_calls_mean: float
    turns_mean: float
    tokens_mean: dict[str, float]
    cost_per_case_usd: float | None
    cost_total_usd: float | None
    served_by: dict[str, int]
    confusion: dict[str, int]  # "label -> predicted" for every wrong answer


def summarise(scores: list[CaseScore], *, model: str) -> Metrics:
    clean = [s for s in scores if s.distractor is None]
    tagged = [s for s in scores if s.distractor is not None]
    groups: dict[str, dict[str, list[CaseScore]]] = defaultdict(lambda: defaultdict(list))
    for s in scores:
        groups["code"][s.code.value].append(s)
        groups["reference"][s.reference].append(s)
        if s.distractor:
            groups["distractor"][s.distractor].append(s)
    costs = [s.cost_usd for s in scores]
    priced = [c for c in costs if c is not None]
    total = sum(priced) if len(priced) == len(costs) else None

    injected = [bool(s.injection_resisted) for s in scores if s.injection_resisted is not None]

    def share(flag: str) -> float:
        return fmean(bool(getattr(s, flag)) for s in scores)

    return Metrics(
        cases=len(scores),
        accuracy=_rate(scores).accuracy,
        accuracy_clean=_rate(clean).accuracy if clean else None,
        accuracy_distractors=_rate(tagged).accuracy if tagged else None,
        per_code={k: _rate(v) for k, v in sorted(groups["code"].items())},
        per_distractor={k: _rate(v) for k, v in sorted(groups["distractor"].items())},
        per_reference={k: _rate(v) for k, v in sorted(groups["reference"].items())},
        schema_valid=share("schema_valid"),
        evidence_resolves=share("evidence_resolves"),
        key_evidence_recall=fmean(s.key_evidence_recall for s in scores),
        no_patient_identifiers=share("no_patient_identifiers"),
        escalation_correct=share("escalation_correct"),
        no_clinical_advice=share("no_clinical_advice"),
        injection_resisted=fmean(injected) if injected else None,
        outcomes=dict(Counter(s.outcome for s in scores)),
        incomplete=sum(s.outcome not in ("answered", "error") for s in scores),
        errors=sum(s.error is not None for s in scores),
        repairs=sum(s.repairs for s in scores),
        latency_p50_s=percentile([s.duration_ms / 1000 for s in scores], 50),
        latency_p95_s=percentile([s.duration_ms / 1000 for s in scores], 95),
        tool_calls_mean=fmean(s.tool_calls for s in scores),
        turns_mean=fmean(s.turns for s in scores),
        tokens_mean={
            name: fmean(getattr(s, name) for s in scores)
            for name in (
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
                "output_tokens",
                "total_tokens",
            )
        },
        cost_per_case_usd=total / len(scores) if total is not None else None,
        cost_total_usd=total,
        served_by=dict(Counter(m for s in scores for m in s.served_by)),
        confusion=dict(
            Counter(
                f"{s.code.value} -> {s.predicted.value if s.predicted else 'none'}"
                for s in scores
                if not s.root_cause_correct and s.error is None
            )
        ),
    )


class Comparison(BaseModel):
    previous_id: str
    deltas: dict[str, float | None]
    fixed: list[str]  # wrong before, right now
    broke: list[str]  # right before, wrong now


_DELTAS = [
    "accuracy",
    "accuracy_distractors",
    "key_evidence_recall",
    "evidence_resolves",
    "latency_p50_s",
    "latency_p95_s",
    "tool_calls_mean",
    "cost_per_case_usd",
]


class EvalReport(BaseModel):
    report_id: str
    created_at: str
    dataset_version: str
    case_set: str
    model: str
    effort: str
    prompt_version: str
    flags: dict[str, Any]
    git_commit: str
    duration_s: float
    metrics: Metrics
    scores: list[CaseScore]
    comparison: Comparison | None


def compare(current: EvalReport, previous: EvalReport) -> Comparison:
    deltas: dict[str, float | None] = {}
    for name in _DELTAS:
        now, then = getattr(current.metrics, name), getattr(previous.metrics, name)
        deltas[name] = None if now is None or then is None else now - then
    before = {s.case_id: s.root_cause_correct for s in previous.scores}
    fixed = [
        s.case_id for s in current.scores if s.root_cause_correct and before.get(s.case_id) is False
    ]
    broke = [
        s.case_id for s in current.scores if not s.root_cause_correct and before.get(s.case_id)
    ]
    return Comparison(previous_id=previous.report_id, deltas=deltas, fixed=fixed, broke=broke)


def find_previous(
    reports_dir: Path, *, case_set: str, dataset_version: str, before: str
) -> EvalReport | None:
    """The latest report earlier than `before` (a report id) for the same cases.

    A report this code cannot read, such as one written before a field was added, is
    skipped with a warning: a comparison is worth having, but not at the cost of the run.
    """
    for path in sorted(reports_dir.glob("*.json"), reverse=True):
        if path.stem >= before:
            continue
        try:
            candidate = EvalReport.model_validate_json(path.read_text())
        except ValidationError as error:
            print(
                f"sr-evals: skipping {path.name} for the comparison: it is not a report this "
                f"version can read ({error.error_count()} problems)",
                file=sys.stderr,
            )
            continue
        if candidate.case_set == case_set and candidate.dataset_version == dataset_version:
            return candidate
    return None


def write_report(report: EvalReport, reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / f"{report.report_id}.json"
    md_path = reports_dir / f"{report.report_id}.md"
    json_path.write_text(json.dumps(report.model_dump(mode="json"), indent=2) + "\n")
    md_path.write_text(render_markdown(report))
    return json_path, md_path


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _money(value: float | None) -> str:
    return "-" if value is None else f"${value:.3f}"


def _signed(value: float | None, unit: str = "") -> str:
    if value is None:
        return "-"
    if unit == "%":
        return f"{value * 100:+.1f} pts"
    return f"{value:+.3f}{unit}"


def render_markdown(report: EvalReport) -> str:
    m = report.metrics
    lines = [
        f"# Eval report {report.report_id}",
        "",
        f"Model `{report.model}` (effort {report.effort}), prompt {report.prompt_version}, "
        f"dataset {report.dataset_version} ({report.case_set}, {m.cases} cases), "
        f"commit `{report.git_commit}`, {report.created_at}, {report.duration_s:.0f} s.",
    ]
    if report.flags:
        lines.append("Flags: " + ", ".join(f"`{k}={v}`" for k, v in sorted(report.flags.items())))
    lines += [
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Root-cause accuracy | {_pct(m.accuracy)} |",
        f"| Accuracy, clean cases | {_pct(m.accuracy_clean)} |",
        f"| Accuracy, distractor cases | {_pct(m.accuracy_distractors)} |",
        f"| Evidence resolves | {_pct(m.evidence_resolves)} |",
        f"| Key evidence recall | {_pct(m.key_evidence_recall)} |",
        f"| No patient identifiers | {_pct(m.no_patient_identifiers)} |",
        f"| Escalation correct | {_pct(m.escalation_correct)} |",
        f"| No clinical advice | {_pct(m.no_clinical_advice)} |",
        f"| Injections resisted | {_pct(m.injection_resisted)} |",
        f"| Schema valid | {_pct(m.schema_valid)} |",
        f"| Latency p50 / p95 | {m.latency_p50_s:.1f} s / {m.latency_p95_s:.1f} s |",
        f"| Tool calls per case | {m.tool_calls_mean:.1f} |",
        f"| Tokens per case (cached read / total) | "
        f"{m.tokens_mean['cache_read_input_tokens']:,.0f} / {m.tokens_mean['total_tokens']:,.0f} |",
        f"| Cost per case / total | {_money(m.cost_per_case_usd)} / {_money(m.cost_total_usd)} |",
        f"| Incomplete / errors / repairs | {m.incomplete} / {m.errors} / {m.repairs} |",
        "",
        "Outcomes: " + ", ".join(f"{k} {v}" for k, v in sorted(m.outcomes.items())) + ".",
        "Served by: " + ", ".join(f"{k} ({v} turns)" for k, v in sorted(m.served_by.items())) + ".",
    ]
    if report.comparison:
        c = report.comparison
        lines += [
            "",
            f"## Compared with {c.previous_id}",
            "",
            "| Measure | Change |",
            "| --- | --- |",
        ]
        for name, delta in c.deltas.items():
            unit = (
                "%"
                if name
                in ("accuracy", "accuracy_distractors", "key_evidence_recall", "evidence_resolves")
                else ""
            )
            unit = " s" if name.startswith("latency") else unit
            lines.append(f"| {name} | {_signed(delta, unit)} |")
        lines += [
            "",
            f"Now right: {', '.join(c.fixed) or 'none'}. "
            f"Now wrong: {', '.join(c.broke) or 'none'}.",
        ]
    lines += ["", "## Per code", "", "| Code | Correct | Accuracy |", "| --- | --- | --- |"]
    lines += [
        f"| {k} | {r.correct}/{r.cases} | {_pct(r.accuracy)} |" for k, r in m.per_code.items()
    ]
    if m.per_distractor:
        lines += [
            "",
            "## Per distractor",
            "",
            "| Distractor | Correct | Accuracy |",
            "| --- | --- | --- |",
        ]
        lines += [
            f"| {k} | {r.correct}/{r.cases} | {_pct(r.accuracy)} |"
            for k, r in m.per_distractor.items()
        ]
    lines += [
        "",
        "## Per way of naming the referral",
        "",
        "| Reference | Correct | Accuracy |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| {k} | {r.correct}/{r.cases} | {_pct(r.accuracy)} |" for k, r in m.per_reference.items()
    ]
    wrong = [s for s in report.scores if not s.root_cause_correct]
    lines += ["", "## Wrong or incomplete", ""]
    if not wrong:
        lines.append("None.")
    else:
        lines += [
            "| Case | Label | Predicted | Outcome | Key evidence | Tools | Seconds |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for s in wrong:
            predicted = s.predicted.value if s.predicted else s.error or "none"
            lines.append(
                f"| {s.case_id} | {s.code.value} | {predicted} | {s.outcome} "
                f"| {_pct(s.key_evidence_recall)} | {s.tool_calls} | {s.duration_ms / 1000:.1f} |"
            )
    return "\n".join(lines) + "\n"
