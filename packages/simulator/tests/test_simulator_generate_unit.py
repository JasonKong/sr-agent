"""Unit tests for healthy-world generation (T1.2). No database needed."""

import dataclasses
import re
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from sr_db.domain import (
    DeliveryChannel,
    DeliveryStatus,
    ErrorLevel,
    HubReferralStatus,
    InboxFilingStatus,
    RecipientActionType,
    ReferralStatus,
    RoutingMode,
    table_for_id,
)
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.rows import World

NZ = ZoneInfo("Pacific/Auckland")
SMALL = SimConfig(
    seed=7, practices=6, clinics=8, referrals=300, failure_rate=0.0, distractor_rate=0.0
)


@pytest.fixture(scope="module")
def world() -> World:
    return generate(SMALL)


def _all_rows(world: World) -> Iterator[tuple[str, object]]:
    for table, rows in world.tables():
        for row in rows:
            yield table, row


def _datetimes(row: object) -> Iterator[datetime]:
    for field in dataclasses.fields(row):  # type: ignore[arg-type]
        value = getattr(row, field.name)
        if isinstance(value, datetime):
            yield value


# --- Reproducibility ------------------------------------------------------------------------


def test_same_config_gives_identical_worlds(world: World) -> None:
    assert generate(SMALL) == world


def test_a_different_seed_gives_a_different_world(world: World) -> None:
    assert generate(dataclasses.replace(SMALL, seed=8)) != world


# --- Shape ----------------------------------------------------------------------------------


def test_sizes_follow_the_config(world: World) -> None:
    assert len(world.gp_practices) == SMALL.practices
    assert len(world.pms_connections) == SMALL.practices
    assert len(world.clinics) == SMALL.clinics
    assert len(world.referrals) == SMALL.referrals
    assert len(world.patients) == SMALL.referrals
    assert all(2 <= n <= 8 for n in Counter(u.practice_id for u in world.gp_users).values())
    assert all(1 <= n <= 6 for n in Counter(s.clinic_id for s in world.specialists).values())


def test_every_id_has_its_table_prefix_and_is_unique(world: World) -> None:
    for table, rows in world.tables():
        ids = [row.id for row in rows]  # type: ignore[attr-defined]
        assert len(ids) == len(set(ids)), table
        assert all(table_for_id(i) == table for i in ids), table


def test_referral_ids_follow_creation_order(world: World) -> None:
    created = [r.created_at for r in world.referrals]
    assert created == sorted(created)
    assert world.referrals[0].id == "GR-10001"


def test_nothing_happens_after_the_anchor_time(world: World) -> None:
    for table, row in _all_rows(world):
        for moment in _datetimes(row):
            if table in {"composer.pms_connection", "hub.clinic_integration"}:
                continue  # token_expires_at is legitimately in the future
            assert moment <= SMALL.anchor_time, (table, row)


def test_all_timestamps_are_timezone_aware(world: World) -> None:
    for _, row in _all_rows(world):
        assert all(moment.tzinfo is not None for moment in _datetimes(row))


def test_referrals_are_created_in_business_hours(world: World) -> None:
    for referral in world.referrals:
        local = referral.created_at.astimezone(NZ)
        assert local.weekday() < 5
        assert 7 <= local.hour <= 17


def test_synthetic_nhis_can_never_be_real(world: World) -> None:
    # Real NHI numbers never use the letters I or O.
    nhis = [p.synthetic_nhi for p in world.patients]
    assert all(n is not None and re.fullmatch(r"SIM[0-9]{4}", n) for n in nhis)
    assert len(set(nhis)) == len(nhis)


def test_dates_of_birth_use_the_pms_text_format(world: World) -> None:
    for patient in world.patients:
        assert patient.date_of_birth is not None
        datetime.strptime(patient.date_of_birth, "%d-%m-%Y")


def test_most_clinics_have_active_accounts_and_some_have_pms_integrations() -> None:
    world = generate(SimConfig(seed=11, clinics=40, referrals=50))
    with_account = sum(c.has_active_account for c in world.clinics)
    assert 30 <= with_account <= 40
    assert 15 <= len({i.clinic_id for i in world.clinic_integrations}) <= with_account


# --- Healthy lifecycle invariants -----------------------------------------------------------


def test_healthy_referrals_only_go_to_clinics_with_active_accounts(world: World) -> None:
    clinics = {c.id: c for c in world.clinics}
    assert all(clinics[r.recipient_clinic_ref].has_active_account for r in world.referrals)


def test_every_referral_is_healthy_and_consistent_across_both_systems(world: World) -> None:
    hub = {h.id: h for h in world.hub_referrals}
    sends = {s.referral_id: s for s in world.hub_sends}
    files = {f.hub_referral_id: f for f in world.hub_files}
    deliveries: dict[str, list[str]] = {}
    for d in world.deliveries:
        deliveries.setdefault(d.hub_referral_id, []).append(d.id)
    attempts = {a.delivery_id: a for a in world.delivery_attempts}
    actions: dict[str, list[RecipientActionType]] = {}
    for a in world.recipient_actions:
        actions.setdefault(a.hub_referral_id, []).append(a.action)

    for r in world.referrals:
        assert r.error_level is ErrorLevel.NONE
        assert r.status is not ReferralStatus.ERROR
        if r.hub_referral_id is None:
            assert r.status in {
                ReferralStatus.DRAFT,
                ReferralStatus.SUBMITTED,
                ReferralStatus.GENERATED,
            }
            continue

        h = hub[r.hub_referral_id]
        assert (h.source_ref, h.correlation_id) == (r.id, r.correlation_id)
        assert h.clinic_id == r.recipient_clinic_ref
        assert h.specialist_id == r.recipient_specialist_ref
        assert sends[r.id].http_status == 201
        assert r.submitted_at is not None
        assert r.sent_at is not None
        assert r.created_at <= r.submitted_at <= r.sent_at <= h.created_at

        if h.accepted_at is None:
            assert r.status is ReferralStatus.SENT
            continue
        assert h.routing_mode is RoutingMode.ELECTRONIC
        assert files[h.id].fetch_status.value == "FETCHED"
        assert h.created_at <= files[h.id].updated_at <= h.accepted_at
        for delivery_id in deliveries.get(h.id, []):
            if delivery_id in attempts:
                assert attempts[delivery_id].http_status in {200, 201, 202}

        expected_hub_status = {
            ReferralStatus.ACKNOWLEDGED: HubReferralStatus.ACKNOWLEDGED,
            ReferralStatus.DECLINED: HubReferralStatus.DECLINED,
        }.get(r.status)
        if expected_hub_status is not None:
            assert h.status is expected_hub_status
        if RecipientActionType.VIEWED in actions.get(h.id, []):
            assert h.status is not HubReferralStatus.NEW


def test_accepted_referrals_are_delivered_on_every_channel(world: World) -> None:
    integrations: dict[str, set[str]] = {}
    for i in world.clinic_integrations:
        integrations.setdefault(i.clinic_id, set()).add(i.id)
    attempted = {a.delivery_id for a in world.delivery_attempts}
    by_referral: dict[str, list[tuple[DeliveryChannel, str]]] = {}
    for d in world.deliveries:
        # A delivery scheduled after the anchor time is still pending, with no attempt yet.
        if d.status is DeliveryStatus.PENDING:
            assert d.id not in attempted
        else:
            assert d.status is DeliveryStatus.SUCCEEDED
            assert d.id in attempted
        by_referral.setdefault(d.hub_referral_id, []).append((d.channel, d.target_ref))

    for h in world.hub_referrals:
        if h.accepted_at is None:
            continue
        got = by_referral[h.id]
        assert {c for c, _ in got} >= {DeliveryChannel.EMAIL, DeliveryChannel.PORTAL}
        pms_targets = {t for c, t in got if c is DeliveryChannel.CLINIC_PMS}
        assert pms_targets == integrations.get(h.clinic_id, set())


def test_every_response_returns_to_the_gp_and_is_filed(world: World) -> None:
    returned = {c.action_id for c in world.return_callbacks}
    filed = {i.hub_action_ref: i for i in world.inbound_responses}
    for action in world.recipient_actions:
        if action.action is RecipientActionType.DOWNLOADED:
            assert action.id not in returned
            continue
        if action.id in filed:
            assert filed[action.id].inbox_filing_status is InboxFilingStatus.FILED
            assert action.id in returned


def test_events_and_children_follow_their_referral_in_time(world: World) -> None:
    created = {r.id: r.created_at for r in world.referrals}
    for event in world.referral_events:
        assert event.occurred_at >= created[event.referral_id]
    hub_created = {h.id: h.created_at for h in world.hub_referrals}
    for action in world.recipient_actions:
        assert action.occurred_at >= hub_created[action.hub_referral_id]


def test_outcomes_have_a_realistic_mix(world: World) -> None:
    statuses = Counter(r.status for r in world.referrals)
    finished = statuses[ReferralStatus.ACKNOWLEDGED] + statuses[ReferralStatus.DECLINED]
    assert finished > 0.7 * SMALL.referrals
    assert 0.75 <= statuses[ReferralStatus.ACKNOWLEDGED] / finished <= 0.97
    in_flight = SMALL.referrals - finished
    assert in_flight > 0, "recent referrals should still be in progress at the anchor time"


def test_event_ids_follow_time_order_within_a_referral(world: World) -> None:
    by_referral: dict[str, list[tuple[str, datetime]]] = {}
    for e in world.referral_events:
        by_referral.setdefault(e.referral_id, []).append((e.id, e.occurred_at))
    for events in by_referral.values():
        in_id_order = [at for _, at in sorted(events, key=lambda e: int(e[0].removeprefix("rev_")))]
        assert in_id_order == sorted(in_id_order)


def test_hub_numbers_are_not_a_fixed_offset_of_composer_numbers(world: World) -> None:
    # The Hub also receives referrals from other channels, so its numbering has gaps; the
    # agent must look the link up rather than guess it.
    offsets = Counter(
        int(r.hub_referral_id.removeprefix("HR-")) - int(r.id.removeprefix("GR-"))
        for r in world.referrals
        if r.hub_referral_id is not None
    )
    assert offsets.most_common(1)[0][1] < 0.2 * len(world.referrals)
