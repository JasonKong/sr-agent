"""The answer schema, evidence ids and answer checks (no model, no network)."""

import json
from typing import Any

import pytest

from sr_agent.diagnosis import (
    Diagnosis,
    check_answer,
    ids_in,
    model_output_schema,
    record_kind,
)
from sr_agent.testing import diagnosis_json


@pytest.mark.parametrize(
    ("record_id", "kind"),
    [
        ("GR-10234", ("referral", "composer")),
        ("HR-55210", ("hub_referral", "hub")),
        ("dl_8812", ("delivery", "hub")),
        ("da_17", ("delivery_attempt", "hub")),
        ("rev_301", ("referral_event", "composer")),
        ("ir_9", ("inbound_response", "composer")),
        ("cln_4", ("clinic", "hub")),
        ("hub-worker:009472", ("log", "hub")),
        ("composer-web:000012", ("log", "composer")),
        ("tkt_77", ("support_ticket", "knowledge")),
    ],
)
def test_the_id_says_what_record_it_is(record_id: str, kind: tuple[str, str]) -> None:
    assert record_kind(record_id) == kind


@pytest.mark.parametrize(
    "record_id",
    ["delivery 8812", "dl-8812", "xx_12", "PT-3f9a1c", "pt_12", "mars-rover:001", "GR-"],
)
def test_unknown_or_patient_ids_are_not_evidence(record_id: str) -> None:
    assert record_kind(record_id) is None


def test_ids_are_found_in_compact_json_and_in_log_messages() -> None:
    text = json.dumps(
        {
            "referral": {"id": "GR-10234", "hub_referral_id": "HR-55210"},
            "deliveries": [{"id": "dl_1", "attempts": [{"id": "da_2"}]}],
            "lines": [{"id": "hub-worker:000123", "message": "push for HR-55210 failed"}],
            "patient": "PT-3f9a1c",
        },
        separators=(",", ":"),
    )
    assert ids_in(text) == {"GR-10234", "HR-55210", "dl_1", "da_2", "hub-worker:000123"}


def _objects(schema: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            found.append(schema)
        for value in schema.values():
            found += _objects(value)
    elif isinstance(schema, list):
        for value in schema:
            found += _objects(value)
    return found


def test_the_output_schema_fits_structured_outputs() -> None:
    schema = model_output_schema()
    objects = _objects(schema)

    assert objects
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])  # every field is required
    text = json.dumps(schema)
    for unsupported in ("minLength", "maxLength", "minimum", "maximum", "minItems"):
        assert unsupported not in text
    assert "F07_CLINIC_PMS_AUTH_FAILED" in text


def test_a_valid_answer_gets_record_types_and_systems_from_its_ids() -> None:
    text = diagnosis_json(evidence=[("dl_1", "CLINIC_PMS failed"), ("hub-worker:000123", "auth")])
    check = check_answer(text, seen={"dl_1", "hub-worker:000123"})

    assert check.problems == []
    assert isinstance(check.diagnosis, Diagnosis)
    assert [(e.record_type, e.record_id, e.system) for e in check.diagnosis.evidence] == [
        ("delivery", "dl_1", "hub"),
        ("log", "hub-worker:000123", "hub"),
    ]
    assert check.diagnosis.root_cause_code == "F07_CLINIC_PMS_AUTH_FAILED"


def test_evidence_the_run_never_saw_is_unresolved() -> None:
    text = diagnosis_json(evidence=[("dl_1", "seen"), ("dl_999", "invented"), ("note", "x")])
    check = check_answer(text, seen={"dl_1"})

    assert check.unresolved == ["dl_999", "note"]
    assert check.diagnosis is not None
    assert "dl_999" in check.problems[0]


def test_not_enough_evidence_is_a_null_code() -> None:
    check = check_answer(diagnosis_json(root_cause_code=None, evidence=[]), seen=set())
    assert check.diagnosis is not None
    assert check.diagnosis.root_cause_code is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The clinic token expired.", "not valid JSON"),
        (diagnosis_json(root_cause_code="F99_MADE_UP"), "root_cause_code"),
        (json.dumps({"summary": "only a summary"}), "confidence"),
    ],
)
def test_answers_that_break_the_schema_are_rejected(text: str, expected: str) -> None:
    check = check_answer(text, seen=set())
    assert check.diagnosis is None
    assert expected in " ".join(check.problems)
