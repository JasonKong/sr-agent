"""Leg tools: the deliveries to the clinic, the recipient clinic, and the return path."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from mcp.server import MCPServer
from pydantic import Field

from sr_mcp.adapters.base import (
    ClinicInfo,
    DeliveryInfo,
    IntegrationInfo,
    PersonInfo,
    ReturnStep,
)
from sr_mcp.deps import Deps
from sr_mcp.guard import NotFound, ToolResult, guarded_tool
from sr_mcp.tools.referrals import ReferralId

ClinicId = Annotated[
    str, Field(pattern=r"^cln_\d+$", description="A clinic id such as cln_37, from get_referral.")
]


class DeliveriesResult(ToolResult):
    composer_id: str | None
    hub_referral_id: str | None
    routing_mode: str | None
    accepted_at: datetime | None
    note: str | None = Field(description="Why there are no deliveries, when there are none.")
    deliveries: list[DeliveryInfo]


class RecipientResult(ToolResult):
    clinic: ClinicInfo
    routing_mode: Literal["ELECTRONIC", "MANUAL"]
    specialists: list[PersonInfo]
    integrations: list[IntegrationInfo]


class ReturnPathResult(ToolResult):
    composer_id: str | None
    hub_referral_id: str | None
    note: str | None
    steps: list[ReturnStep]


def register(server: MCPServer, deps: Deps) -> None:
    @guarded_tool(server, deps)
    def get_deliveries(referral_id: ReferralId) -> DeliveriesResult:
        """Every delivery of one referral from the Hub to the clinic, with every attempt.

        Use this when the referral reached the Hub but the clinic says it did not arrive,
        or the timeline shows a delivery that failed or is still pending. There is one
        delivery per channel: EMAIL, PORTAL, and CLINIC_PMS for each PMS integration of the
        clinic. Each delivery has a status and its last error code, and lists every attempt
        with its HTTP status (empty when there was no response), error code, message and
        latency.

        A delivery can fail with no attempts at all, when it failed before any request was
        sent. An error on an early attempt followed by a later success is not a failure. When
        there are no deliveries, `note` says why (the Hub has no record of the referral, has
        not accepted it, or routed it MANUAL).

        Next: get_recipient for the clinic's integration health.
        """
        view = deps.store.deliveries(referral_id)
        if view is None:
            raise NotFound("referral", referral_id)
        return DeliveriesResult(**view.model_dump())

    @guarded_tool(server, deps)
    def get_recipient(clinic_id: ClinicId) -> RecipientResult:
        """A recipient clinic: whether it can receive electronically, and its PMS integrations.

        Use this to check the clinic side of a delivery problem. It returns:

        - the clinic, including whether it has an active account;
        - the routing mode that follows: ELECTRONIC, or MANUAL with nothing delivered
          electronically;
        - its specialists;
        - each PMS integration: vendor, auth status (VALID, EXPIRED or REVOKED), when its
          token expires, endpoint health (HEALTHY, DEGRADED or DOWN) and when it last changed.

        A problem with an integration affects every referral to the clinic from the time it
        changed. Compare that time with the referral's delivery time before blaming it.
        """
        view = deps.store.recipient(clinic_id)
        if view is None:
            raise NotFound("clinic", clinic_id)
        return RecipientResult(**view.model_dump())

    @guarded_tool(server, deps)
    def get_return_path(referral_id: ReferralId) -> ReturnPathResult:
        """What the specialist did, and whether it got back to the GP.

        Use this when the GP says they never heard back, or to see whether the specialist
        has acted at all. Each step is one recipient action (VIEWED, DOWNLOADED, MESSAGE,
        ACKNOWLEDGED, DECLINED) in time order. It is followed by the Hub's callbacks to
        Composer, and then by the filing of the response in the GP's PMS inbox (FILED,
        FAILED with the error, PENDING or SUPPRESSED).

        Downloads are not returned to the GP, so they have no callback or filing. No steps
        means nobody at the clinic has acted on the referral yet.
        """
        view = deps.store.return_path(referral_id)
        if view is None:
            raise NotFound("referral", referral_id)
        return ReturnPathResult(**view.model_dump())
