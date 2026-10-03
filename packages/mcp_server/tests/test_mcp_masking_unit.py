"""Unit tests for patient masking and the output scrubber."""

import pytest

from sr_mcp.masking import REDACTED, patient_ref, scrub, scrub_text


def test_patient_ref_is_stable_short_and_salted() -> None:
    ref = patient_ref("pt_10234", "salt-a")

    assert ref == patient_ref("pt_10234", "salt-a")
    assert ref != patient_ref("pt_10235", "salt-a")
    assert ref != patient_ref("pt_10234", "salt-b")
    assert ref.startswith("PT-")
    assert len(ref) == 9


@pytest.mark.parametrize(
    "text",
    [
        "NHI SIM0123 on file",  # synthetic NHI
        "patient ABC1234",  # real NHI, old format
        "patient ZZZ12AB",  # real NHI, new format
        "DOB 01-02-1980",
        "born 31/12/1999",
        "dob 07.06.2004",
    ],
)
def test_scrub_redacts_patient_identifiers(text: str) -> None:
    cleaned, hits = scrub_text(text)

    assert hits == 1
    assert REDACTED in cleaned


@pytest.mark.parametrize(
    "text",
    [
        "GR-10234 and HR-55210",
        "dl_538183 da_5381831 pt_10234",
        "2026-10-01T09:00:00.000Z",
        "HTTP 503 in 1155 ms",
        "corr-c3678f38638a1e38",
        "hub-worker:009472",
        "PDF is 16000000 bytes",
        "AUTH_TOKEN_EXPIRED",
        "PT-1a2b3c",
    ],
)
def test_scrub_leaves_ids_times_and_codes_alone(text: str) -> None:
    assert scrub_text(text) == (text, 0)


def test_scrub_walks_nested_structures() -> None:
    value = {"a": ["ok", {"b": "SIM0001 and 01-02-1980"}], "n": 3, "none": None}

    cleaned, hits = scrub(value)

    assert hits == 2
    assert cleaned == {"a": ["ok", {"b": f"{REDACTED} and {REDACTED}"}], "n": 3, "none": None}
