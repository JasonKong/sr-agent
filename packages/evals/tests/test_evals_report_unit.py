"""Metrics, run-to-run comparison and the report files."""

from pathlib import Path
from typing import Any

import pytest

from sr_db.domain import FailureCode
from sr_evals.report import (
    EvalReport,
    compare,
    find_previous,
    percentile,
    render_markdown,
    summarise,
    write_report,
)
from sr_evals.scorers import CaseScore

F07 = FailureCode.F07_CLINIC_PMS_AUTH_FAILED
F08 = FailureCode.F08_CLINIC_PMS_TIMEOUT
F00 = FailureCode.F00_NO_FAULT


def score(
    case_id: str, code: FailureCode, predicted: FailureCode | None, **extra: Any
) -> CaseScore:
    fields: dict[str, Any] = {
        "case_id": case_id,
        "code": code,
        "distractor": None,
        "reference": "composer_id",
        "outcome": "answered",
        "predicted": predicted,
        "confidence": "high",
        "root_cause_correct": predicted == code,
        "schema_valid": True,
        "evidence_resolves": True,
        "key_evidence_recall": 1.0,
        "no_patient_identifiers": True,
        "escalation_correct": True,
        "no_clinical_advice": True,
        "injection_resisted": None,
        "tool_calls": 4,
        "turns": 3,
        "repairs": 0,
        "duration_ms": 10_000.0,
        "input_tokens": 1_000,
        "cache_creation_input_tokens": 10_000,
        "cache_read_input_tokens": 30_000,
        "output_tokens": 2_000,
        "total_tokens": 43_000,
        # 1,000 x $4 + 10,000 x $5 + 30,000 x $0.20 + 2,000 x $20, per million tokens
        "cost_usd": 0.1,
        "served_by": ["claude-opus-5-5"] * 3,
    }
    return CaseScore(**(fields | extra))


SCORES = [
    score("F07-1", F07, F07, duration_ms=8_000.0),
    score("F07-2", F07, F08, duration_ms=12_000.0, key_evidence_recall=0.5),
    score("F08-1", F08, F08, duration_ms=20_000.0, tool_calls=8),
    score("D-in_progress-1", F00, F00, distractor="in_progress", duration_ms=30_000.0),
    score(
        "D-recovered_pms_auth-1",
        F00,
        F07,
        distractor="recovered_pms_auth",
        outcome="tool_limit",
        duration_ms=60_000.0,
    ),
]


def report(report_id: str, scores: list[CaseScore], **extra: Any) -> EvalReport:
    fields: dict[str, Any] = {
        "report_id": report_id,
        "created_at": "2026-10-05T10:00:00Z",
        "dataset_version": "v1",
        "case_set": "all",
        "model": "claude-opus-5-5",
        "effort": "medium",
        "prompt_version": "v1",
        "flags": {},
        "git_commit": "abc1234",
        "duration_s": 100.0,
        "metrics": summarise(scores, model="claude-opus-5-5"),
        "scores": scores,
        "comparison": None,
    }
    return EvalReport(**(fields | extra))


def test_percentiles_use_the_nearest_rank() -> None:
    values = [8.0, 12.0, 20.0, 30.0, 60.0]
    assert percentile(values, 50) == 20.0
    assert percentile(values, 95) == 60.0
    assert percentile([5.0], 95) == 5.0


def test_accuracy_overall_per_code_and_on_distractors() -> None:
    m = summarise(SCORES, model="claude-opus-5-5")

    assert m.cases == 5
    assert m.accuracy == pytest.approx(3 / 5)
    assert m.accuracy_clean == pytest.approx(2 / 3)
    assert m.accuracy_distractors == pytest.approx(1 / 2)
    assert (
        m.per_code["F07_CLINIC_PMS_AUTH_FAILED"].correct,
        m.per_code["F07_CLINIC_PMS_AUTH_FAILED"].cases,
    ) == (1, 2)
    assert m.per_distractor["recovered_pms_auth"].accuracy == 0.0
    assert m.key_evidence_recall == pytest.approx((1 + 0.5 + 1 + 1 + 1) / 5)
    assert m.outcomes == {"answered": 4, "tool_limit": 1}
    assert m.incomplete == 1
    assert sorted(m.confusion.items()) == [
        ("F00_NO_FAULT -> F07_CLINIC_PMS_AUTH_FAILED", 1),
        ("F07_CLINIC_PMS_AUTH_FAILED -> F08_CLINIC_PMS_TIMEOUT", 1),
    ]


def test_latency_tools_tokens_and_cost() -> None:
    m = summarise(SCORES, model="claude-opus-5-5")

    assert m.latency_p50_s == 20.0
    assert m.latency_p95_s == 60.0
    assert m.tool_calls_mean == pytest.approx((4 * 4 + 8) / 5)
    assert m.cost_per_case_usd == pytest.approx(0.1)
    assert m.cost_total_usd == pytest.approx(0.5)
    assert m.served_by == {"claude-opus-5-5": 15}
    unpriced = [*SCORES[:-1], SCORES[-1].model_copy(update={"cost_usd": None})]
    assert summarise(unpriced, model="m").cost_per_case_usd is None


def test_errors_count_as_wrong_and_are_reported() -> None:
    failed = score(
        "F07-3", F07, None, outcome="error", schema_valid=False, error="ModelUnavailable"
    )
    m = summarise([*SCORES, failed], model="claude-opus-5-5")

    assert m.errors == 1
    assert m.accuracy == pytest.approx(3 / 6)


def test_a_comparison_shows_what_moved_and_which_cases_flipped() -> None:
    before = report("r1", SCORES)
    after_scores = [
        score("F07-1", F07, F07),
        score("F07-2", F07, F07),
        score("F08-1", F08, F07),
        *SCORES[3:],
    ]
    after = report("r2", after_scores)
    comparison = compare(after, before)

    assert comparison.previous_id == "r1"
    assert comparison.fixed == ["F07-2"]
    assert comparison.broke == ["F08-1"]
    assert comparison.deltas["accuracy"] == pytest.approx(0.0)


def test_the_previous_report_is_the_latest_for_the_same_cases(tmp_path: Path) -> None:
    write_report(report("20261001-090000-a", SCORES), tmp_path)
    write_report(report("20261002-090000-b", SCORES), tmp_path)
    write_report(report("20261003-090000-c", SCORES, case_set="smoke"), tmp_path)
    write_report(report("20261004-090000-d", SCORES, dataset_version="v2"), tmp_path)

    previous = find_previous(tmp_path, case_set="all", dataset_version="v1", before="20261005")
    assert previous is not None
    assert previous.report_id == "20261002-090000-b"
    assert find_previous(tmp_path, case_set="all", dataset_version="v1", before="20261001") is None


def test_both_files_are_written_and_the_markdown_leads_with_the_headline(tmp_path: Path) -> None:
    current = report("r2", SCORES, comparison=compare(report("r2", SCORES), report("r1", SCORES)))
    json_path, md_path = write_report(current, tmp_path)

    assert EvalReport.model_validate_json(json_path.read_text()) == current
    markdown = md_path.read_text()
    assert markdown == render_markdown(current)
    assert markdown.startswith("# Eval report r2")
    for text in (
        "| Root-cause accuracy | 60.0% |",
        "F07_CLINIC_PMS_AUTH_FAILED",
        "D-recovered_pms_auth-1",
        "Compared with r1",
        "claude-opus-5-5",
    ):
        assert text in markdown


def test_safety_measures_are_reported() -> None:
    scores = [
        *SCORES,
        score(
            "D-injection_in_note-1",
            F00,
            F00,
            distractor="injection_in_note",
            injection_resisted=True,
        ),
        score(
            "D-injection_in_note-2",
            F07,
            F00,
            distractor="injection_in_note",
            injection_resisted=False,
        ),
        score("F07-9", F07, F07, no_clinical_advice=False),
    ]
    m = summarise(scores, model="claude-opus-5-5")
    assert m.no_clinical_advice == pytest.approx(7 / 8)
    assert m.injection_resisted == pytest.approx(1 / 2)
    assert summarise(SCORES, model="m").injection_resisted is None
    markdown = render_markdown(report("r", scores))
    assert "| No clinical advice | 87.5% |" in markdown
    assert "| Injections resisted | 50.0% |" in markdown
