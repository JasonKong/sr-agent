"""What a run will cost, before it starts (`sr-evals run --estimate`, DESIGN.md section 10).

A paid run is announced with its case count and estimated cost before it starts. The
estimate per case is the latest paid report's for the same model, effort, knowledge tools
and dataset, measured at list prices; before any paid run, the planning figures below.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from sr_evals.report import EvalReport

# Planning figures per case without RAG, from DESIGN.md section 10: a prefix of about
# 5,000 tokens, five model calls, six tool calls and 3,500 output tokens at medium effort.
PLANNING_COST_PER_CASE: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"low": 0.11, "medium": 0.15, "high": 0.25},
    "claude-sonnet-5-5": {"medium": 0.09},
    "claude-haiku-4-5": {"n/a": 0.04},
}
RAG_FACTOR = 1.15  # two more tool calls and their results
CAP_FACTOR = 1.5  # the suggested --max-cost-usd, over the estimate


def per_case_estimate(
    reports_dir: Path, *, model: str, effort: str, no_rag: bool, dataset_version: str
) -> tuple[float, str] | None:
    """The cost of one case and where the figure comes from, or None if nothing fits."""
    for path in sorted(reports_dir.glob("*.json"), reverse=True):
        try:
            report = EvalReport.model_validate_json(path.read_text())
        except ValidationError:
            continue
        same = (report.model, report.effort, bool(report.flags.get("no_rag")))
        if (
            not report.flags.get("dry_run")
            and same == (model, effort, no_rag)
            and report.dataset_version == dataset_version
            and report.metrics.cost_per_case_usd is not None
        ):
            return report.metrics.cost_per_case_usd, f"measured in {report.report_id}"
    base = PLANNING_COST_PER_CASE.get(model, {}).get(effort)
    if base is None:
        return None
    return base * (1.0 if no_rag else RAG_FACTOR), "planning estimate"
