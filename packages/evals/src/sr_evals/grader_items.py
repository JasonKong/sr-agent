"""What the rubric grades: calibration items with known labels, and a run's real answers.

**Synthetic calibration items** come from the scenarios, so their labels are known by
construction. For the first clean case of each code:
- a summary in the scenario's own root-cause words passes; the same answer as a code and
  record ids with no plain explanation fails;
- the scenario's recommended action passes; another cause's action fails;
- for a few codes, a paraphrase of the action passes, and an action with an essential
  step missing fails.

Known negatives come once each: an empty summary or next step, "I don't know", and a
summary about another referral. Synthetic items are easier than real mistakes, so a
grader is also calibrated on real answers labelled by hand (`grader_labels_v1.json`).

The set is frozen to `packages/evals/datasets/grader_calibration_v1.json`; a test rebuilds
it, so a scenario's changed wording changes it on purpose, never silently.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from sr_db.domain import FailureCode
from sr_evals.dataset import DATASET_DIR, Dataset, EvalCase
from sr_evals.grader import GradeItem, Label
from sr_simulator.scenario import load_scenarios

CALIBRATION_V1 = DATASET_DIR / "grader_calibration_v1.json"
HAND_LABELS_V1 = DATASET_DIR / "grader_labels_v1.json"

# Hand-written variants. A paraphrase keeps every essential action in other words; a
# partial step drops one, so following it would not fix the problem.
PARAPHRASES: dict[FailureCode, str] = {
    FailureCode.F00_NO_FAULT: "Nothing needs fixing: tell the customer what the current status is.",
    FailureCode.F06_MANUAL_ROUTING: (
        "Let the GP know this clinic can't receive referrals electronically, so they need to "
        "send it another way, and the clinic may want to set up an account."
    ),
    FailureCode.F07_CLINIC_PMS_AUTH_FAILED: (
        "Get the clinic to reconnect its PMS to the Hub, and once that is done, send the "
        "referral again."
    ),
    FailureCode.F11_RESPONSE_NOT_FILED: (
        "Let the GP know the specialist has replied, get them to check their PMS connection, "
        "then file the response into their inbox again."
    ),
}
PARTIALS: dict[FailureCode, tuple[str, str]] = {
    FailureCode.F04_HUB_UNAVAILABLE: (
        "Resend the referral once the Hub is healthy.",
        "drops the escalation to engineering with the correlation id",
    ),
    FailureCode.F07_CLINIC_PMS_AUTH_FAILED: (
        "Resend the referral.",
        "drops the re-authorisation, so the resend would fail the same way",
    ),
    FailureCode.F08_CLINIC_PMS_TIMEOUT: (
        "Resend the referral now.",
        "drops checking that the endpoint is healthy, so the resend would time out again",
    ),
}


class CalibrationSet(BaseModel):
    version: str
    items: list[GradeItem]


def _other_leg(code: FailureCode) -> FailureCode:
    """The next code, in catalogue order, whose leg differs: a different fix."""
    scenarios = load_scenarios()
    codes = list(FailureCode)
    start = codes.index(code)
    for step in range(1, len(codes)):
        other = codes[(start + step) % len(codes)]
        if scenarios[other].leg != scenarios[code].leg:
            return other
    raise AssertionError("every scenario is on the same leg")


def _summary(case: EvalCase, kind: str, text: str, label: Label, note: str) -> GradeItem:
    return GradeItem(
        item_id=f"syn-{case.case_id}-summary-{kind}",
        criterion="summary_clear",
        case_id=case.case_id,
        source="synthetic",
        question=case.question,
        summary=text,
        label=label,
        note=note,
    )


def _step(
    case: EvalCase, reference: str, kind: str, text: str, label: Label, note: str
) -> GradeItem:
    return GradeItem(
        item_id=f"syn-{case.case_id}-step-{kind}",
        criterion="next_step_correct",
        case_id=case.case_id,
        source="synthetic",
        reference_action=reference,
        recommended_action=text,
        label=label,
        note=note,
    )


def calibration_items(dataset: Dataset) -> list[GradeItem]:
    scenarios = load_scenarios()
    by_id = {c.case_id: c for c in dataset.cases}
    items: list[GradeItem] = []
    for code in FailureCode:
        case = by_id[f"{code.value[:3]}-1"]
        spec = scenarios[code]
        action = spec.recommended_action.strip()

        ids = ", ".join(e.record_id for e in case.expected_evidence[:3])
        wrong = _other_leg(code)
        items += [
            _summary(case, "good", spec.root_cause.strip(), "pass", "the scenario's root cause"),
            _summary(
                case,
                "jargon",
                f"{code.value}: {ids} confirm it. See the records.",
                "fail",
                "a code and record ids, nothing in plain words (item 2)",
            ),
            _step(case, action, "good", action, "pass", "the reference itself"),
            _step(
                case,
                action,
                "wrong",
                scenarios[wrong].recommended_action.strip(),
                "fail",
                f"the fix for {wrong.value}, another leg",
            ),
        ]
        if code in PARAPHRASES:
            reworded = PARAPHRASES[code]
            items.append(
                _step(case, action, "paraphrase", reworded, "pass", "the reference reworded")
            )
        if code in PARTIALS:
            text, why = PARTIALS[code]
            items.append(_step(case, action, "partial", text, "fail", why))

    f07 = by_id["F07-1"]
    other = by_id["F00-1"].composer_id
    reference = scenarios[FailureCode.F07_CLINIC_PMS_AUTH_FAILED].recommended_action.strip()
    known = [
        ("syn-empty-summary", "summary_clear", "", "empty"),
        (
            "syn-unknown-summary",
            "summary_clear",
            "I don't know.",
            "says only that it does not know",
        ),
        (
            "syn-other-referral-summary",
            "summary_clear",
            f"Referral {other} was delivered and acknowledged; nothing is wrong with it.",
            f"about {other}, not the referral asked about (item 1)",
        ),
        ("syn-empty-step", "next_step_correct", "", "empty"),
        (
            "syn-unknown-step",
            "next_step_correct",
            "I don't know.",
            "says only that it does not know",
        ),
    ]
    for item_id, criterion, text, note in known:
        fields = (
            {"question": f07.question, "summary": text}
            if criterion == "summary_clear"
            else {"reference_action": reference, "recommended_action": text}
        )
        items.append(
            GradeItem(
                item_id=item_id,
                criterion=criterion,
                case_id=f07.case_id,
                source="synthetic",
                label="fail",
                note=f"a known negative: {note}",
                **fields,
            )
        )
    return items


def write_calibration(items: Sequence[GradeItem], path: Path = CALIBRATION_V1) -> None:
    document = CalibrationSet(version="calibration_v1", items=list(items))
    path.write_text(json.dumps(document.model_dump(mode="json"), indent=2) + "\n")


def load_calibration(path: Path = CALIBRATION_V1, labels: Path | None = None) -> list[GradeItem]:
    """The synthetic set, then any hand-labelled items (`labels`, if the file exists)."""
    items = CalibrationSet.model_validate_json(path.read_text()).items
    if labels is not None and labels.exists():
        items += CalibrationSet.model_validate(
            {"version": "labels", **json.loads(labels.read_text())}
        ).items
    return items


def report_items(
    case_ids: Sequence[str], runs_dir: Path, dataset: Dataset, *, report_id: str
) -> tuple[list[GradeItem], list[str]]:
    """Two items per answered case (summary, next step), and the cases with no answer."""
    scenarios = load_scenarios()
    by_id = {c.case_id: c for c in dataset.cases}
    items: list[GradeItem] = []
    no_answer: list[str] = []
    for case_id in case_ids:
        body = json.loads((runs_dir / f"{case_id}.json").read_text())
        diagnosis = body.get("diagnosis")
        if not diagnosis:
            no_answer.append(case_id)
            continue
        case = by_id[case_id]
        source = f"report:{report_id}"
        items += [
            GradeItem(
                item_id=f"{case_id}-summary",
                criterion="summary_clear",
                case_id=case_id,
                source=source,
                question=case.question,
                summary=diagnosis["summary"],
            ),
            GradeItem(
                item_id=f"{case_id}-step",
                criterion="next_step_correct",
                case_id=case_id,
                source=source,
                reference_action=scenarios[case.code].recommended_action.strip(),
                recommended_action=diagnosis["recommended_action"],
            ),
        ]
    return items, no_answer
