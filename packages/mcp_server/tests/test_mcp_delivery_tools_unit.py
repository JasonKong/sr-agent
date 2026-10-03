"""Unit tests for get_deliveries, get_recipient and get_return_path, on fake data."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp import Client

from sr_mcp.adapters.base import (
    CallbackInfo,
    ClinicInfo,
    DeliveriesView,
    DeliveryAttemptInfo,
    DeliveryInfo,
    InboxFiling,
    IntegrationInfo,
    PersonInfo,
    RecipientView,
    ReturnPathView,
    ReturnStep,
)
from sr_mcp.server import create_server
from sr_mcp.testing import FakeReferralStore, fake_deps

T0 = datetime(2026, 9, 24, 9, 38, tzinfo=UTC)

DELIVERIES = DeliveriesView(
    composer_id="GR-11854",
    hub_referral_id="HR-53818",
    routing_mode="ELECTRONIC",
    accepted_at=T0,
    note=None,
    deliveries=[
        DeliveryInfo(
            id="dl_538181", channel="EMAIL", target_ref="clinic-email", status="SUCCEEDED",
            last_error_code=None, updated_at=T0,
            attempts=[
                DeliveryAttemptInfo(
                    id="da_5381811", attempt_no=1, http_status=202, error_code=None,
                    error_message=None, latency_ms=900, attempted_at=T0,
                )
            ],
        ),
        DeliveryInfo(
            id="dl_538183", channel="CLINIC_PMS", target_ref="ci_17", status="FAILED",
            last_error_code="AUTH_TOKEN_EXPIRED", updated_at=T0, attempts=[],
        ),
    ],
)  # fmt: skip
NO_HUB = DeliveriesView(
    composer_id="GR-10021",
    hub_referral_id=None,
    routing_mode=None,
    accepted_at=None,
    note="The Hub has no record of this referral, so nothing was delivered.",
    deliveries=[],
)
RECIPIENT = RecipientView(
    clinic=ClinicInfo(
        id="cln_37", name="Kereru ENT Specialists", specialty_group="ENT", status="ACTIVE",
        has_active_account=True,
    ),
    routing_mode="ELECTRONIC",
    specialists=[PersonInfo(id="spc_1", name="Dr B Test")],
    integrations=[
        IntegrationInfo(
            id="ci_17", vendor="KAURI_PMS", auth_status="EXPIRED", token_expires_at=T0,
            endpoint_status="HEALTHY", updated_at=T0,
        )
    ],
)  # fmt: skip
RETURN = ReturnPathView(
    composer_id="GR-10008",
    hub_referral_id="HR-50009",
    note=None,
    steps=[
        ReturnStep(
            action_id="ra_500091", action="VIEWED", actor_ref="spc_1", occurred_at=T0,
            callbacks=[CallbackInfo(id="rc_5000911", attempt_no=1, http_status=200,
                                    status="SUCCEEDED", attempted_at=T0)],
            gp_inbox=InboxFiling(id="ir_1000801", type="VIEWED", received_at=T0,
                                 inbox_filing_status="FILED", filing_error=None),
        ),
        ReturnStep(
            action_id="ra_500092", action="DOWNLOADED", actor_ref="spc_1", occurred_at=T0,
            callbacks=[], gp_inbox=None,
        ),
    ],
)  # fmt: skip

STORE = FakeReferralStore(
    deliveries={"GR-11854": DELIVERIES, "HR-53818": DELIVERIES, "GR-10021": NO_HUB},
    recipients={"cln_37": RECIPIENT},
    return_paths={"GR-10008": RETURN},
)


def _call(name: str, args: dict[str, Any]) -> Any:
    async def run() -> Any:
        async with Client(create_server(fake_deps(store=STORE))) as client:
            return await client.call_tool(name, args)

    return asyncio.run(run())


def _tools() -> dict[str, Any]:
    async def run() -> Any:
        async with Client(create_server(fake_deps())) as client:
            return {t.name: t for t in (await client.list_tools()).tools}

    return asyncio.run(run())


@pytest.mark.parametrize("name", ["get_deliveries", "get_recipient", "get_return_path"])
def test_tools_are_registered_with_useful_descriptions(name: str) -> None:
    tool = _tools()[name]
    assert len(tool.description) > 120
    assert "Use" in tool.description


def test_clinic_ids_are_pattern_checked() -> None:
    schema = _tools()["get_recipient"].input_schema["properties"]["clinic_id"]
    assert schema["pattern"] == r"^cln_\d+$"
    assert _call("get_recipient", {"clinic_id": "Kereru"}).is_error


def test_get_deliveries_lists_channels_and_attempts() -> None:
    data = _call("get_deliveries", {"referral_id": "HR-53818"}).structured_content

    assert data["hub_referral_id"] == "HR-53818"
    pms = next(d for d in data["deliveries"] if d["channel"] == "CLINIC_PMS")
    assert pms["status"] == "FAILED"
    assert pms["attempts"] == []
    email = next(d for d in data["deliveries"] if d["channel"] == "EMAIL")
    assert email["attempts"][0]["http_status"] == 202


def test_no_deliveries_come_with_a_reason() -> None:
    data = _call("get_deliveries", {"referral_id": "GR-10021"}).structured_content

    assert data["deliveries"] == []
    assert data["hub_referral_id"] is None
    assert "no record" in data["note"]


def test_get_recipient_shows_integration_health() -> None:
    data = _call("get_recipient", {"clinic_id": "cln_37"}).structured_content

    assert data["routing_mode"] == "ELECTRONIC"
    assert data["integrations"][0]["auth_status"] == "EXPIRED"
    assert data["specialists"][0]["id"] == "spc_1"


def test_get_return_path_follows_each_action_back_to_the_gp() -> None:
    data = _call("get_return_path", {"referral_id": "GR-10008"}).structured_content

    viewed, downloaded = data["steps"]
    assert viewed["callbacks"][0]["status"] == "SUCCEEDED"
    assert viewed["gp_inbox"]["inbox_filing_status"] == "FILED"
    assert downloaded["callbacks"] == []
    assert downloaded["gp_inbox"] is None


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("get_deliveries", {"referral_id": "GR-99999"}),
        ("get_recipient", {"clinic_id": "cln_999"}),
        ("get_return_path", {"referral_id": "HR-1"}),
    ],
)
def test_unknown_ids_are_not_found(name: str, args: dict[str, Any]) -> None:
    result = _call(name, args)
    assert result.is_error
