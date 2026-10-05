"""Output safety: patient identifiers and clinical advice (DESIGN.md sections 7.4 and 8)."""

import pytest

from sr_agent.diagnosis import Diagnosis, Evidence
from sr_agent.safety import clinical_advice, mask_identifiers, scrub_question


def diagnosis(**fields: object) -> Diagnosis:
    base: dict[str, object] = {
        "summary": "Toroa Surgical's PMS token expired, so the push failed.",
        "root_cause_code": "F07_CLINIC_PMS_AUTH_FAILED",
        "confidence": "high",
        "evidence": [Evidence(record_type="delivery", record_id="dl_1", system="hub", detail="d")],
        "recommended_action": "Ask the clinic to re-authorise its PMS connection, then resend.",
        "draft_customer_reply": "Kereru ENT Specialists will get the referral once it is resent.",
        "escalate_to_engineering": False,
    }
    return Diagnosis.model_validate(base | fields)


def test_identifiers_in_the_question_are_masked_before_the_model_sees_them() -> None:
    masked, hits = scrub_question("Patient SIM0042, born 03-04-1985: why didn't GR-10234 arrive?")
    assert masked == "Patient [REDACTED], born [REDACTED]: why didn't GR-10234 arrive?"
    assert hits == 2


def test_identifiers_in_the_answer_are_masked_and_counted() -> None:
    found = diagnosis(summary="Patient ABC1234 (born 03-04-1985) was affected.")
    cleaned, hits = mask_identifiers(found)
    assert hits == 2
    assert "ABC1234" not in cleaned.model_dump_json()
    assert cleaned.evidence == found.evidence


def test_ordinary_cs_answers_are_not_clinical_advice() -> None:
    assert clinical_advice(diagnosis()) == []


@pytest.mark.parametrize(
    "text",
    [
        "The patient should see a cardiologist urgently.",
        "We recommend starting medication while the referral is resent.",
        "Consider increasing the dose before the appointment.",
        "This needs urgent clinical review.",
        "The specialist should triage this as a priority.",
        "Prescribe antibiotics in the meantime.",
    ],
)
def test_clinical_advice_anywhere_in_the_answer_is_flagged(text: str) -> None:
    for field in ("summary", "recommended_action", "draft_customer_reply"):
        flags = clinical_advice(diagnosis(**{field: text}))
        assert flags, (field, text)
        assert flags[0].startswith(f"{field}:")
