"""Unit tests for get_referral, find_referrals and get_referral_timeline, on fake data."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

from sr_mcp.adapters.base import (
    ClinicInfo,
    ComposerSide,
    HubSide,
    PatientFields,
    PersonInfo,
    PracticeInfo,
    Recipient,
    ReferralSummary,
    ReferralView,
    TimelineEntry,
)
from sr_mcp.masking import FieldStatus
from sr_mcp.server import create_server
from sr_mcp.testing import FakeReferralStore, fake_deps

T0 = datetime(2026, 9, 24, 9, 38, tzinfo=UTC)

VIEW = ReferralView(
    link="linked",
    correlation_id="corr-1",
    composer=ComposerSide(
        id="GR-11854",
        status="RECEIVED",
        error_level="NONE",
        created_at=T0,
        submitted_at=T0,
        sent_at=T0,
        pdf_size_bytes=360553,
        practice=PracticeInfo(
            id="prc_3", name="Rimu Road Medical Practice", pms_vendor="KAURI_PMS"
        ),
        gp_user=PersonInfo(id="gpu_1", name="Dr A Test", role="GP"),
        patient=PatientFields(
            patient_ref="PT-abc123",
            synthetic_nhi=FieldStatus.PRESENT,
            date_of_birth=FieldStatus.PRESENT,
        ),
    ),
    hub=HubSide(
        id="HR-53818", status="NEW", routing_mode="ELECTRONIC", created_at=T0, accepted_at=T0
    ),
    recipient=Recipient(
        clinic=ClinicInfo(
            id="cln_21", name="Tui Cardiology", specialty_group="CARDIOLOGY", status="ACTIVE",
            has_active_account=True,
        ),
        specialist=None,
    ),
)  # fmt: skip


def _entry(n: int) -> TimelineEntry:
    return TimelineEntry(
        at=T0 + timedelta(seconds=n),
        system="hub" if n % 2 else "composer",
        leg="ingest",
        event="EVENT_" + str(n),
        level="INFO",
        detail=f"step {n} " + "x" * 40,
        record_id=f"rev_{n}",
    )


def _summary(n: int) -> ReferralSummary:
    return ReferralSummary(
        composer_id=f"GR-1{n:04d}",
        hub_id=None,
        created_at=T0,
        composer_status="SENT",
        hub_status=None,
        error_level="NONE",
        has_error=False,
        clinic_name="Tui Cardiology",
        practice_name="Rimu Road Medical Practice",
        correlation_id=f"corr-{n}",
    )


STORE = FakeReferralStore(
    views={"GR-11854": VIEW, "HR-53818": VIEW},
    summaries=[_summary(n) for n in range(30)],
    timelines={
        "GR-11854": [_entry(n) for n in range(3)],
        "GR-10001": [_entry(n) for n in range(80)],
    },
)


def _call(name: str, args: dict[str, Any], max_chars: int = 8000) -> Any:
    server = create_server(fake_deps(store=STORE, max_result_chars=max_chars))

    async def run() -> Any:
        async with Client(server) as client:
            return await client.call_tool(name, args)

    return asyncio.run(run())


def _text(result: Any) -> str:
    return "".join(c.text for c in result.content if isinstance(c, TextContent))


def _tools() -> dict[str, Any]:
    async def run() -> Any:
        async with Client(create_server(fake_deps())) as client:
            return {t.name: t for t in (await client.list_tools()).tools}

    return asyncio.run(run())


# --- Schemas and descriptions ----------------------------------------------------------------


def test_the_three_referral_tools_are_registered() -> None:
    assert {"get_referral", "find_referrals", "get_referral_timeline"} <= set(_tools())


def test_referral_ids_are_validated_by_the_input_schema() -> None:
    schema = _tools()["get_referral"].input_schema["properties"]["referral_id"]
    assert schema["pattern"] == r"^(GR|HR)-\d+$"


@pytest.mark.parametrize("name", ["get_referral", "find_referrals", "get_referral_timeline"])
def test_descriptions_tell_the_model_when_to_use_the_tool(name: str) -> None:
    description = _tools()[name].description
    assert len(description) > 120
    assert "Use" in description


# --- get_referral ----------------------------------------------------------------------------


def test_get_referral_returns_the_linked_view() -> None:
    result = _call("get_referral", {"referral_id": "GR-11854"})

    data = result.structured_content
    assert data["referral"]["link"] == "linked"
    assert data["referral"]["hub"]["id"] == "HR-53818"
    assert data["referral"]["composer"]["patient"]["patient_ref"] == "PT-abc123"


def test_get_referral_by_hub_id_gives_the_same_view() -> None:
    by_gr = _call("get_referral", {"referral_id": "GR-11854"}).structured_content
    by_hr = _call("get_referral", {"referral_id": "HR-53818"}).structured_content
    assert by_gr == by_hr


def test_unknown_referral_is_not_found() -> None:
    result = _call("get_referral", {"referral_id": "GR-99999"})
    assert result.is_error
    assert "NOT_FOUND" in _text(result)


@pytest.mark.parametrize("bad", ["11854", "gr-11854", "XR-1", "GR-", "GR-12 OR 1=1"])
def test_malformed_ids_are_rejected_before_reaching_the_store(bad: str) -> None:
    assert _call("get_referral", {"referral_id": bad}).is_error


# --- find_referrals ---------------------------------------------------------------------------


def test_find_referrals_passes_filters_and_counts() -> None:
    result = _call(
        "find_referrals",
        {
            "clinic": "Tui",
            "with_errors": True,
            "created_from": "2026-09-01",
            "created_to": "2026-09-30",
            "limit": 5,
        },
    )

    data = result.structured_content
    assert data["count"] == 5
    assert len(data["referrals"]) == 5
    filters = STORE.last_filters
    assert filters is not None
    assert (filters.clinic, filters.with_errors, filters.limit) == ("Tui", True, 5)
    assert (filters.created_from, filters.created_to) == (date(2026, 9, 1), date(2026, 9, 30))


@pytest.mark.parametrize("limit", [0, 21])
def test_find_referrals_limit_is_bounded(limit: int) -> None:
    assert _call("find_referrals", {"limit": limit}).is_error


def test_find_referrals_rejects_unknown_statuses() -> None:
    assert _call("find_referrals", {"status": "LOST"}).is_error


# --- get_referral_timeline --------------------------------------------------------------------


def test_timeline_returns_entries_in_time_order() -> None:
    data = _call("get_referral_timeline", {"referral_id": "GR-11854"}).structured_content

    assert data["referral_id"] == "GR-11854"
    times = [e["at"] for e in data["entries"]]
    assert times == sorted(times)
    assert {e["record_id"] for e in data["entries"]} == {"rev_0", "rev_1", "rev_2"}


def test_long_timelines_keep_the_earliest_entries_and_say_so() -> None:
    data = _call(
        "get_referral_timeline", {"referral_id": "GR-10001"}, max_chars=3000
    ).structured_content

    assert data["truncated"] is True
    assert data["entries"][0]["record_id"] == "rev_0"
    assert len(data["entries"]) < 80


def test_unknown_timeline_is_not_found() -> None:
    assert _call("get_referral_timeline", {"referral_id": "HR-1"}).is_error
