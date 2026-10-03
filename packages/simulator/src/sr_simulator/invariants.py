"""Healthy-lifecycle invariants, used to check that F00 referrals stay healthy.

Returns human-readable violations rather than raising, so a test can show all of them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from sr_db.domain import (
    DeliveryChannel,
    DeliveryStatus,
    ErrorLevel,
    FetchStatus,
    HubReferralStatus,
    InboxFilingStatus,
    ReferralStatus,
    RoutingMode,
)
from sr_simulator.rows import Delivery, World

_FINAL = {
    ReferralStatus.ACKNOWLEDGED: HubReferralStatus.ACKNOWLEDGED,
    ReferralStatus.DECLINED: HubReferralStatus.DECLINED,
}


def healthy_violations(world: World, referral_ids: Iterable[str]) -> list[str]:
    hub = {h.id: h for h in world.hub_referrals}
    clinics = {c.id: c for c in world.clinics}
    sends: dict[str, list[int | None]] = defaultdict(list)
    for s in world.hub_sends:
        sends[s.referral_id].append(s.http_status)
    files = {f.hub_referral_id: f for f in world.hub_files}
    deliveries: dict[str, list[Delivery]] = defaultdict(list)
    for d in world.deliveries:
        deliveries[d.hub_referral_id].append(d)
    attempted = {a.delivery_id for a in world.delivery_attempts}
    integrations: dict[str, set[str]] = defaultdict(set)
    for i in world.clinic_integrations:
        integrations[i.clinic_id].add(i.id)
    filing: dict[str, list[InboxFilingStatus]] = defaultdict(list)
    for response in world.inbound_responses:
        filing[response.referral_id].append(response.inbox_filing_status)
    failed_docs = {
        d.referral_id for d in world.source_documents if d.fetch_status is FetchStatus.FAILED
    }

    wanted = set(referral_ids)
    problems: list[str] = []
    for r in world.referrals:
        if r.id not in wanted:
            continue

        def bad(message: str, rid: str = r.id) -> None:
            problems.append(f"{rid}: {message}")

        if r.error_level is not ErrorLevel.NONE or r.status is ReferralStatus.ERROR:
            bad(f"status {r.status}, error level {r.error_level}")
        if not clinics[r.recipient_clinic_ref].has_active_account:
            bad("sent to a clinic without an active account")
        if r.id in failed_docs:
            bad("a source document failed to fetch")
        if any(s != InboxFilingStatus.FILED for s in filing[r.id]):
            bad("a response was not filed")
        if r.hub_referral_id is None:
            if r.status not in {
                ReferralStatus.DRAFT,
                ReferralStatus.SUBMITTED,
                ReferralStatus.GENERATED,
            }:
                bad(f"status {r.status} without a Hub referral")
            if sends[r.id]:
                bad("hand-off attempts without a Hub referral")
            continue

        h = hub[r.hub_referral_id]
        if (h.source_ref, h.correlation_id, h.clinic_id) != (
            r.id,
            r.correlation_id,
            r.recipient_clinic_ref,
        ):
            bad("Composer and Hub records disagree")
        if sends[r.id] != [201]:
            bad(f"hand-off attempts {sends[r.id]}")
        if r.submitted_at is None or r.sent_at is None:
            bad("missing submitted or sent time")
        elif not r.created_at <= r.submitted_at <= r.sent_at <= h.created_at:
            bad("timeline out of order")
        if h.accepted_at is None:
            if r.status is not ReferralStatus.SENT:
                bad(f"status {r.status} before the Hub accepted it")
            continue
        if h.routing_mode is not RoutingMode.ELECTRONIC:
            bad(f"routing {h.routing_mode}")
        if files[h.id].fetch_status is not FetchStatus.FETCHED:
            bad("PDF not fetched by the Hub")
        channels = set()
        for d in deliveries[h.id]:
            channels.add((d.channel, d.target_ref))
            ok = d.status is DeliveryStatus.SUCCEEDED and d.id in attempted
            pending = d.status is DeliveryStatus.PENDING and d.id not in attempted
            if not (ok or pending):
                bad(f"delivery {d.id} is {d.status}")
        pms = {t for c, t in channels if c is DeliveryChannel.CLINIC_PMS}
        if pms != integrations[h.clinic_id]:
            bad(f"clinic PMS deliveries {sorted(pms)} != integrations")
        expected = _FINAL.get(r.status)
        if expected is not None and h.status is not expected:
            bad(f"Hub status {h.status} for Composer status {r.status}")
    return problems
