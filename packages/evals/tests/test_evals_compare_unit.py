"""Comparing two runs on the same cases: the paired sign test and its verdict."""

from typing import Any

import pytest

from sr_db.domain import FailureCode
from sr_evals.compare import sign_test, verdict
from sr_evals.report import EvalReport, compare, render_markdown, summarise
from sr_evals.scorers import CaseScore

F07 = FailureCode.F07_CLINIC_PMS_AUTH_FAILED
F08 = FailureCode.F08_CLINIC_PMS_TIMEOUT


@pytest.mark.parametrize(
    ("fixed", "broke", "p"),
    [
        (6, 0, 0.03125),  # 2 x (1/2)^6
        (0, 6, 0.03125),  # two-sided: the direction does not matter
        (9, 1, 0.021484375),  # 2 x (1 + 10) / 1024
        (8, 4, 0.3876953125),
        (5, 5, 1.0),
        (0, 0, 1.0),  # nothing changed: no evidence either way
    ],
)
def test_the_sign_test_is_exact_and_two_sided(fixed: int, broke: int, p: float) -> None:
    assert sign_test(fixed, broke) == pytest.approx(p)


@pytest.mark.parametrize(
    ("fixed", "broke", "expected"),
    [
        (6, 0, "better"),  # p = 0.031
        (0, 6, "worse"),
        (10, 2, "better"),  # p = 0.039
        (6, 1, "possibly better"),  # p = 0.125, net 5
        (0, 4, "possibly worse"),  # p = 0.125, net 4
        (5, 1, "within noise"),  # p = 0.219
        (3, 0, "within noise"),  # p = 0.25
        (8, 4, "within noise"),  # p = 0.39
        (0, 0, "within noise"),
    ],
)
def test_the_verdict_follows_the_rule(fixed: int, broke: int, expected: str) -> None:
    assert verdict(fixed, broke) == expected


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
        "cost_usd": 0.1,
        "served_by": ["claude-opus-5-5"] * 3,
    }
    return CaseScore(**(fields | extra))


def report(report_id: str, scores: list[CaseScore]) -> EvalReport:
    return EvalReport(
        report_id=report_id,
        created_at="2026-10-05T10:00:00Z",
        dataset_version="v1",
        case_set="all",
        model="claude-opus-5-5",
        effort="medium",
        prompt_version="v1",
        flags={},
        git_commit="abc1234",
        duration_s=100.0,
        metrics=summarise(scores, model="claude-opus-5-5"),
        scores=scores,
        comparison=None,
    )


def test_a_case_with_an_error_in_either_run_is_left_out_of_the_test() -> None:
    error = {"outcome": "error", "error": "ModelUnavailable: 529"}
    before = report(
        "r1",
        [
            score("F07-1", F07, None, **error),  # an outage before: not a wrong answer
            score("F07-2", F07, F08),
            score("F07-3", F07, F07),
            score("F08-1", F08, F08),
        ],
    )
    after = report(
        "r2",
        [
            score("F07-1", F07, F07),
            score("F07-2", F07, F07),  # fixed
            score("F07-3", F07, None, **error),  # an outage now: not broken
            score("F08-1", F08, F07),  # broke
        ],
    )
    comparison = compare(after, before)

    assert comparison.fixed == ["F07-2"]
    assert comparison.broke == ["F08-1"]
    assert comparison.excluded == ["F07-1", "F07-3"]
    assert comparison.p_value == pytest.approx(1.0)
    assert comparison.verdict == "within noise"


def test_cases_in_only_one_run_are_not_compared() -> None:
    before = report("r1", [score("F07-1", F07, F08)])
    after = report("r2", [score("F07-1", F07, F07), score("F08-1", F08, F07)])
    comparison = compare(after, before)

    assert comparison.fixed == ["F07-1"]
    assert comparison.broke == []


def test_the_markdown_states_the_verdict_with_its_evidence() -> None:
    before = report("r1", [score(f"F07-{n}", F07, F08) for n in range(1, 7)])
    after = report("r2", [score(f"F07-{n}", F07, F07) for n in range(1, 7)])
    current = after.model_copy(update={"comparison": compare(after, before)})

    text = render_markdown(current)
    assert "Verdict: **better** (6 fixed, 0 broke; sign test p = 0.031)" in text
