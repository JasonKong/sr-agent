"""Referral tools: look one up across both systems, find some, and see what happened when."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from mcp.server import MCPServer
from pydantic import Field

from sr_mcp.adapters.base import ReferralFilters, ReferralSummary, ReferralView, TimelineEntry
from sr_mcp.deps import Deps
from sr_mcp.guard import NotFound, ToolResult, guarded_tool

ReferralId = Annotated[
    str,
    Field(
        pattern=r"^(GR|HR)-\d+$",
        description="A Composer referral id (GR-10234) or a Hub referral id (HR-55210).",
    ),
]
ComposerStatus = Literal[
    "DRAFT", "SUBMITTED", "GENERATED", "SENT", "RECEIVED", "ACKNOWLEDGED", "DECLINED", "ERROR"
]


class ReferralResult(ToolResult):
    referral: ReferralView


class FindReferralsResult(ToolResult):
    count: int = Field(description="How many referrals are listed (at most the limit).")
    referrals: list[ReferralSummary]


class TimelineResult(ToolResult):
    referral_id: str
    entries: list[TimelineEntry]


def register(server: MCPServer, deps: Deps) -> None:
    @guarded_tool(server, deps)
    def get_referral(referral_id: ReferralId) -> ReferralResult:
        """Look up one referral in both systems at once.

        Use this first for any question about a specific referral. It accepts a Composer id
        (GR-...) or a Hub id (HR-...) and returns both sides linked:

        - Composer: status, error level, key times, GP practice and GP; the source
          documents pulled from the GP's PMS (with fetch status); and each hand-off attempt
          to the Hub (with HTTP status).
        - Patient: a masked reference (PT-...), and whether the NHI and date of birth are
          present, empty or invalid. Names and values are never returned.
        - Hub: status, routing mode (ELECTRONIC or MANUAL), created and accepted times.
        - Recipient clinic (with whether it has an active account) and specialist.
        - The correlation id shared by both systems and their logs.

        `link` is "linked" when both systems have the referral, "composer_only" when the
        Hub has no record of it, and "hub_only" when only the Hub knows it.

        Next: get_referral_timeline to see what happened when.
        """
        view = deps.store.get_referral(referral_id)
        if view is None:
            raise NotFound("referral", referral_id)
        return ReferralResult(referral=view)

    @guarded_tool(server, deps)
    def find_referrals(
        clinic: Annotated[
            str | None, Field(description="Recipient clinic id (cln_...) or part of its name.")
        ] = None,
        practice: Annotated[
            str | None, Field(description="GP practice id (prc_...) or part of its name.")
        ] = None,
        status: Annotated[ComposerStatus | None, Field(description="Composer status.")] = None,
        with_errors: Annotated[
            bool | None,
            Field(
                description="True: only referrals with an error recorded in either system (a "
                "Composer error, a failed Hub file fetch or a failed delivery). Silent problems, "
                "such as a referral nobody has acted on, record no error."
            ),
        ] = None,
        created_from: Annotated[
            date | None, Field(description="Created on or after this day (UTC).")
        ] = None,
        created_to: Annotated[
            date | None, Field(description="Created on or before this day (UTC).")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=20, description="At most 20.")] = 20,
    ) -> FindReferralsResult:
        """Find referrals when you do not have an id, newest first.

        Use this when the question names a clinic, a GP practice or a date instead of a
        referral id, or to see whether other recent referrals to the same clinic have the
        same problem. Combine filters to narrow the list. Each result gives both ids, both
        statuses, the error level, whether either system recorded an error, the clinic and
        practice names, and the correlation id.

        Next: get_referral on the referral that matches the question.
        """
        filters = ReferralFilters(
            clinic=clinic,
            practice=practice,
            status=status,
            with_errors=with_errors,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
        )
        found = deps.store.find_referrals(filters)
        return FindReferralsResult(count=len(found), referrals=found)

    @guarded_tool(server, deps)
    def get_referral_timeline(referral_id: ReferralId) -> TimelineResult:
        """Everything that happened to one referral, in both systems, in time order (UTC).

        Use this after get_referral to find the leg where things went wrong. Composer
        entries come from its event history (submitted, PDF generated, sent to the Hub,
        resends, responses filed). Hub entries are its milestones: referral received, PDF
        fetched, accepted and routed, the final state of each delivery channel (with the
        last error code), recipient actions (viewed, acknowledged, declined) and callbacks
        to Composer.

        Each entry has a system, a leg (compose, ingest, deliver, respond, return), an event,
        a level and a record_id to cite as evidence. Individual delivery attempts are not
        listed here; use the delivery tools for those.
        """
        entries = deps.store.timeline(referral_id)
        if entries is None:
            raise NotFound("referral", referral_id)
        return TimelineResult(referral_id=referral_id, entries=entries)
