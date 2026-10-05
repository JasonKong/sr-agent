"""Chunking tickets and runbooks for retrieval: masked, stable ids, nothing lost."""

from datetime import UTC, datetime

from sr_db.domain import FailureCode, KnowledgeSource
from sr_mcp.knowledge.chunking import runbook_chunks, ticket_chunk
from sr_simulator.rows import Runbook, SupportTicket

WHEN = datetime(2026, 9, 1, tzinfo=UTC)

RUNBOOK = Runbook(
    id="rb_7",
    slug="clinic-pms-auth-failed",
    error_code=FailureCode.F07_CLINIC_PMS_AUTH_FAILED,
    title="Clinic PMS authorisation failed",
    body_markdown="""\
---
slug: clinic-pms-auth-failed
error_code: F07_CLINIC_PMS_AUTH_FAILED
title: Clinic PMS authorisation failed
---
# Clinic PMS authorisation failed

## Symptoms

- The specialist says the referral never arrived in their PMS.

## How to confirm

- Clinic integration: auth status `EXPIRED`.
- The delivery has **no** attempts.

### A subtle case

A single 401 followed by `TOKEN_REFRESHED` is not this fault.

## What to do

Ask the clinic to re-authorise.
""",
    updated_at=WHEN,
)


def ticket(description: str, resolution: str | None = "Re-authorised; resent.") -> SupportTicket:
    return SupportTicket(
        id="tkt_42",
        referral_ref="GR-10001",
        clinic_ref="cln_3",
        title="Clinic not getting anything",
        description=description,
        resolution=resolution,
        root_cause_code=FailureCode.F07_CLINIC_PMS_AUTH_FAILED,
        created_at=WHEN,
        resolved_at=WHEN,
    )


def test_a_ticket_is_one_chunk_with_its_resolution() -> None:
    chunk = ticket_chunk(ticket("The specialist never got GR-10001."))

    assert chunk.id == "tkt_42#0"
    assert chunk.source_type is KnowledgeSource.TICKET
    assert chunk.error_code == "F07_CLINIC_PMS_AUTH_FAILED"
    assert chunk.title == "Clinic not getting anything"
    assert "The specialist never got GR-10001." in chunk.text
    assert "Resolution: Re-authorised; resent." in chunk.text
    assert chunk.metadata["clinic_ref"] == "cln_3"
    assert chunk.redacted == 0


def test_patient_identifiers_are_masked_before_anything_else() -> None:
    chunk = ticket_chunk(ticket("Patient SIM0042, born 03-04-1985, is missing.", None))

    assert "SIM0042" not in chunk.text
    assert "03-04-1985" not in chunk.text
    assert chunk.redacted == 2
    assert "Resolution" not in chunk.text


def test_runbook_sections_merge_up_to_the_size_limit() -> None:
    [whole] = runbook_chunks(RUNBOOK, max_chars=2000)
    small = runbook_chunks(RUNBOOK, max_chars=120)

    assert whole.id == "rb_7#0"
    assert whole.source_type is KnowledgeSource.RUNBOOK
    assert whole.error_code == "F07_CLINIC_PMS_AUTH_FAILED"
    assert whole.title == "Clinic PMS authorisation failed"
    assert "slug:" not in whole.text  # front matter is not content
    # "How to confirm" is too long, so it is split, and its pieces stand alone; the
    # second piece sits under "A subtle case". Whole short sections merge with each other.
    assert [c.id for c in small] == ["rb_7#0", "rb_7#1", "rb_7#2", "rb_7#3"]
    assert [c.metadata["headings"] for c in small] == [
        ["Symptoms"],
        ["How to confirm", "A subtle case"],
        ["How to confirm", "A subtle case"],
        ["What to do"],
    ]
    assert small[1].title == "Clinic PMS authorisation failed > How to confirm"
    assert small[2].text.startswith("A single 401")
    [merged] = runbook_chunks(RUNBOOK, max_chars=400)[:1]
    assert merged.metadata["headings"][0] == "Symptoms"


def test_no_runbook_text_is_lost_whatever_the_size() -> None:
    for size in (2000, 400, 120, 40):
        text = " ".join(c.text for c in runbook_chunks(RUNBOOK, max_chars=size))
        for line in (
            "never arrived in their PMS",
            "**no** attempts",
            "TOKEN_REFRESHED",
            "re-authorise",
        ):
            assert line in text, (size, line)


def test_chunks_are_the_same_every_time() -> None:
    first = runbook_chunks(RUNBOOK, max_chars=400)
    second = runbook_chunks(RUNBOOK, max_chars=400)
    assert first == second
    assert first[0].content_hash("hash-384") == second[0].content_hash("hash-384")
    assert first[0].content_hash("hash-384") != first[0].content_hash("bge-small")
