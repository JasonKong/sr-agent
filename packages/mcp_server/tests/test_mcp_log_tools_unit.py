"""Unit tests for search_logs and list_failed_referrals, on fake data."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

from sr_mcp.adapters.base import (
    ComposerSide,
    FailedReferral,
    LogRecord,
    PatientFields,
    PersonInfo,
    PracticeInfo,
    Recipient,
    ReferralView,
)
from sr_mcp.masking import FieldStatus
from sr_mcp.server import create_server
from sr_mcp.testing import FakeLogSource, FakeReferralStore, fake_deps

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _record(n: int, level: str, service: str, corr: str, message: str = "msg") -> LogRecord:
    return LogRecord(
        id=f"{service}:{n:06d}",
        timestamp=NOW - timedelta(minutes=n),
        level=level,
        service=service,
        referral_id="GR-11854" if service.startswith("composer") else "HR-53818",
        correlation_id=corr,
        leg="deliver",
        event="EVENT",
        error_code=None,
        message=message,
    )


VIEW = ReferralView(
    link="composer_only",
    correlation_id="corr-1",
    composer=ComposerSide(
        id="GR-11854", status="SENT", error_level="NONE", created_at=NOW, submitted_at=NOW,
        sent_at=NOW, pdf_size_bytes=1,
        practice=PracticeInfo(id="prc_1", name="P", pms_vendor="KAURI_PMS"),
        gp_user=PersonInfo(id="gpu_1", name="Dr G"),
        patient=PatientFields(patient_ref="PT-1", synthetic_nhi=FieldStatus.PRESENT,
                              date_of_birth=FieldStatus.PRESENT),
    ),
    hub=None,
    recipient=Recipient(clinic=None, specialist=None),
)  # fmt: skip
LOGS = FakeLogSource(
    [
        _record(1, "ERROR", "hub-worker", "corr-1"),
        _record(2, "INFO", "composer-worker", "corr-1", "x" * 1000),
        _record(3, "WARN", "hub-api", "corr-2"),
    ]
)
FAILURES = [
    FailedReferral(
        composer_id="GR-11854", hub_id="HR-53818", leg="deliver",
        reason="CLINIC_PMS to ci_17 failed: AUTH_TOKEN_EXPIRED",
        failed_at=NOW - timedelta(hours=2), clinic_name="Kereru ENT Specialists",
    ),
    FailedReferral(
        composer_id="GR-10021", hub_id=None, leg="ingest",
        reason="Hand-off to the Hub failed: HTTP 503 (2 attempts)",
        failed_at=NOW - timedelta(days=3), clinic_name="Tui Cardiology",
    ),
]  # fmt: skip
STORE = FakeReferralStore(views={"GR-11854": VIEW}, failures=FAILURES, now=NOW)


def _call(name: str, args: dict[str, Any]) -> Any:
    async def run() -> Any:
        async with Client(create_server(fake_deps(store=STORE, logs=LOGS))) as client:
            return await client.call_tool(name, args)

    return asyncio.run(run())


def _text(result: Any) -> str:
    return "".join(c.text for c in result.content if isinstance(c, TextContent))


# --- search_logs -------------------------------------------------------------------------------


def test_a_referral_id_finds_logs_from_both_systems() -> None:
    data = _call("search_logs", {"referral_id": "GR-11854"}).structured_content

    assert data["correlation_id"] == "corr-1"
    assert {line["service"] for line in data["lines"]} == {"hub-worker", "composer-worker"}
    assert data["lines"][0]["timestamp"] > data["lines"][1]["timestamp"]  # newest first


def test_long_messages_are_shortened_and_marked() -> None:
    data = _call("search_logs", {"correlation_id": "corr-1"}).structured_content
    long = next(line for line in data["lines"] if line["service"] == "composer-worker")
    assert len(long["message"]) <= 300
    assert long["message_truncated"] is True


def test_min_level_filters() -> None:
    data = _call(
        "search_logs", {"correlation_id": "corr-1", "min_level": "ERROR"}
    ).structured_content
    assert [line["level"] for line in data["lines"]] == ["ERROR"]


def test_an_id_is_required() -> None:
    result = _call("search_logs", {"service": "hub-worker"})
    assert result.is_error
    assert "referral_id or a correlation_id" in _text(result)


@pytest.mark.parametrize(
    "args", [{"correlation_id": "nope"}, {"referral_id": "GR-1", "limit": 201}]
)
def test_inputs_are_validated(args: dict[str, Any]) -> None:
    assert _call("search_logs", args).is_error


def test_unknown_referral_is_not_found() -> None:
    assert "NOT_FOUND" in _text(_call("search_logs", {"referral_id": "GR-99999"}))


# --- list_failed_referrals -----------------------------------------------------------------------


def test_failures_in_the_window_relative_to_the_data() -> None:
    data = _call("list_failed_referrals", {"hours": 24}).structured_content

    assert data["as_of"].startswith("2026-10-01T09:00")
    assert [f["composer_id"] for f in data["failures"]] == ["GR-11854"]
    assert data["counts"] == {"deliver": 1}


def test_wider_window_and_leg_filter() -> None:
    everything = _call("list_failed_referrals", {"hours": 24 * 7}).structured_content
    assert everything["counts"] == {"deliver": 1, "ingest": 1}

    ingest = _call("list_failed_referrals", {"hours": 24 * 7, "leg": "ingest"}).structured_content
    assert [f["leg"] for f in ingest["failures"]] == ["ingest"]


def test_the_description_says_what_is_not_listed() -> None:
    async def run() -> Any:
        async with Client(create_server(fake_deps())) as client:
            return {t.name: t for t in (await client.list_tools()).tools}

    description = asyncio.run(run())["list_failed_referrals"].description
    assert "MANUAL" in description
    assert "not listed" in description
