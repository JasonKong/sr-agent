"""Contract tests: the Postgres ReferralStore agrees with the simulator's ground truth.

Runs through the read-only role against a seeded throwaway database, and compares every
answer with what the simulator generated (`case_rows`).
"""

import re
from collections.abc import Iterator
from datetime import UTC, timedelta

import psycopg
import pytest

from sr_db.domain import FailureCode
from sr_mcp.adapters.base import ReferralFilters, TimelineEntry
from sr_mcp.adapters.postgres import PostgresReferralStore
from sr_simulator.case_rows import CaseRows, case_rows
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.rows import World
from sr_simulator.write import write_world

pytestmark = pytest.mark.integration

WORLD = SimConfig(
    seed=13, practices=8, clinics=14, referrals=600, failure_rate=0.2, distractor_rate=0.1
)


@pytest.fixture(scope="module")
def world() -> World:
    return generate(WORLD)


@pytest.fixture
def store(
    migrated: psycopg.Connection, reader_url: str, world: World
) -> Iterator[PostgresReferralStore]:
    write_world(migrated, world)
    store = PostgresReferralStore(reader_url, mask_salt="contract")
    yield store
    store.close()


def _sample(world: World) -> list[str]:
    """A few referrals of every failure code, every distractor kind, and clean F00 cases."""
    picks: list[str] = []
    for code in FailureCode:
        ids = sorted(r for r, c in world.labels.items() if c is code)
        if code is FailureCode.F00_NO_FAULT:
            ids = [r.id for r in world.referrals if r.id not in world.labels][::50]
        picks += ids[:3]
    for kind in sorted(set(world.distractors.values())):
        picks += sorted(r for r, k in world.distractors.items() if k == kind)[:2]
    return picks


def _field_status(value: str | None, pattern: str) -> str:
    if not value:
        return "empty"
    return "present" if re.fullmatch(pattern, value) else "invalid"


# --- get_referral --------------------------------------------------------------------------------


def test_get_referral_matches_the_simulator(store: PostgresReferralStore, world: World) -> None:
    for referral_id in _sample(world):
        rows = case_rows(world, referral_id)
        view = store.get_referral(referral_id)
        assert view is not None, referral_id
        r = rows.referral
        assert view.composer is not None
        assert (view.composer.id, view.composer.status, view.composer.error_level) == (
            r.id,
            r.status.value,
            r.error_level.value,
        )
        assert view.correlation_id == r.correlation_id
        assert view.composer.practice.id == r.practice_id
        assert view.composer.gp_user.id == r.gp_user_id
        patient = rows.patients[0]
        assert view.composer.patient.synthetic_nhi == _field_status(
            patient.synthetic_nhi, r"[A-Z]{3}\d{4}"
        )
        assert view.composer.patient.date_of_birth == _field_status(
            patient.date_of_birth, r"\d{2}-\d{2}-\d{4}"
        )
        assert view.recipient.clinic is not None
        assert view.recipient.clinic.id == r.recipient_clinic_ref
        if rows.hub_referrals:
            hub = rows.hub_referrals[0]
            assert view.link == "linked"
            assert view.hub is not None
            assert (view.hub.id, view.hub.status) == (hub.id, hub.status.value)
            assert view.hub.routing_mode == (hub.routing_mode.value if hub.routing_mode else None)
        else:
            assert view.link == "composer_only"
            assert view.hub is None


def test_hub_ids_find_the_same_referral(store: PostgresReferralStore, world: World) -> None:
    for referral in world.referrals[:: len(world.referrals) // 25]:
        if referral.hub_referral_id is None:
            continue
        assert store.get_referral(referral.hub_referral_id) == store.get_referral(referral.id)


def test_f04_referrals_exist_only_in_composer(store: PostgresReferralStore, world: World) -> None:
    f04 = [r for r, c in world.labels.items() if c is FailureCode.F04_HUB_UNAVAILABLE]
    assert f04
    for referral_id in f04:
        view = store.get_referral(referral_id)
        assert view is not None
        assert view.link == "composer_only"


def test_a_hub_record_without_its_composer_referral_is_hub_only(
    store: PostgresReferralStore, migrated: psycopg.Connection, world: World
) -> None:
    clinic = world.clinics[0].id
    migrated.execute(
        "INSERT INTO hub.hub_referral"
        " (id, source_ref, correlation_id, clinic_id, status, created_at)"
        " VALUES ('HR-99999', 'GR-00001', 'corr-orphan', %s, 'NEW', now())",
        (clinic,),
    )

    view = store.get_referral("HR-99999")

    assert view is not None
    assert view.link == "hub_only"
    assert view.composer is None
    assert view.recipient.clinic is not None
    assert view.recipient.clinic.id == clinic


def test_unknown_ids_are_none(store: PostgresReferralStore) -> None:
    assert store.get_referral("GR-99998") is None
    assert store.get_referral("HR-1") is None
    assert store.timeline("GR-99998") is None


# --- find_referrals -------------------------------------------------------------------------------


def test_find_by_clinic_name_is_newest_first_and_limited(
    store: PostgresReferralStore, world: World
) -> None:
    clinic = world.clinics[1]
    part = clinic.name.split()[0].lower()  # case-insensitive, part of the name
    found = store.find_referrals(ReferralFilters(clinic=part, limit=7))

    expected = sorted(
        (
            r
            for r in world.referrals
            if r.recipient_clinic_ref in {c.id for c in world.clinics if part in c.name.lower()}
        ),
        key=lambda r: r.created_at,
        reverse=True,
    )[:7]
    assert [s.composer_id for s in found] == [r.id for r in expected]


def test_find_by_status_and_dates(store: PostgresReferralStore, world: World) -> None:
    day = max(r.created_at for r in world.referrals).date() - timedelta(days=3)
    found = store.find_referrals(
        ReferralFilters(
            status="ACKNOWLEDGED", created_from=day - timedelta(days=10), created_to=day, limit=20
        )
    )
    assert found
    for summary in found:
        assert summary.composer_status == "ACKNOWLEDGED"
        assert day - timedelta(days=10) <= summary.created_at.date() <= day


def test_with_errors_covers_both_systems(store: PostgresReferralStore, world: World) -> None:
    flagged = {
        s.composer_id for s in store.find_referrals(ReferralFilters(with_errors=True, limit=20))
    }
    clean = {
        s.composer_id for s in store.find_referrals(ReferralFilters(with_errors=False, limit=20))
    }
    assert flagged
    assert not flagged & clean
    for referral_id in flagged:
        rows = case_rows(world, referral_id)
        has = (
            rows.referral.error_level.value != "NONE"
            or any(f.fetch_status.value == "FAILED" for f in rows.hub_files)
            or any(d.status.value == "FAILED" for d in rows.deliveries)
        )
        assert has, referral_id


# --- timeline -------------------------------------------------------------------------------------


def _oracle_record_ids(rows: CaseRows) -> set[str]:
    ids = {e.id for e in rows.referral_events}
    for hub in rows.hub_referrals:
        ids.add(hub.id)
    ids |= {f.id for f in rows.hub_files}
    ids |= {d.id for d in rows.deliveries}
    ids |= {a.id for a in rows.recipient_actions}
    ids |= {c.id for c in rows.return_callbacks}
    return ids


def _ordered(entries: list[TimelineEntry]) -> bool:
    return [e.at for e in entries] == sorted(e.at for e in entries)


def test_timeline_covers_both_systems_in_time_order(
    store: PostgresReferralStore, world: World
) -> None:
    for referral_id in _sample(world):
        rows = case_rows(world, referral_id)
        entries = store.timeline(referral_id)
        assert entries is not None
        assert _ordered(entries), referral_id
        assert {e.record_id for e in entries} == _oracle_record_ids(rows), referral_id
        composer = [e for e in entries if e.system == "composer"]
        assert len(composer) == len(rows.referral_events)
        assert all(e.leg in {"compose", "ingest", "deliver", "respond", "return"} for e in entries)


def test_timeline_by_hub_id_is_the_same(store: PostgresReferralStore, world: World) -> None:
    referral = next(r for r in world.referrals if r.hub_referral_id)
    assert referral.hub_referral_id is not None
    assert store.timeline(referral.hub_referral_id) == store.timeline(referral.id)


def test_failed_deliveries_show_as_errors_in_the_timeline(
    store: PostgresReferralStore, world: World
) -> None:
    f07 = sorted(r for r, c in world.labels.items() if c is FailureCode.F07_CLINIC_PMS_AUTH_FAILED)[
        0
    ]
    entries = store.timeline(f07)
    assert entries is not None
    failed = [e for e in entries if e.event == "DELIVERY_FAILED"]
    assert failed
    assert failed[0].level == "ERROR"
    assert failed[0].detail is not None
    assert "AUTH_TOKEN_EXPIRED" in failed[0].detail


def test_times_are_utc(store: PostgresReferralStore, world: World) -> None:
    view = store.get_referral(world.referrals[-1].id)
    assert view is not None
    assert view.composer is not None
    assert view.composer.created_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("code", "flagged"),
    [
        (FailureCode.F01_MISSING_REQUIRED_FIELD, True),  # Composer error
        (FailureCode.F05_ATTACHMENT_FETCH_FAILED, True),  # Hub file fetch failed
        (FailureCode.F07_CLINIC_PMS_AUTH_FAILED, True),  # failed delivery, Composer unaware
        (FailureCode.F09_CLINIC_IMPORT_REJECTED, True),  # failed delivery
        (FailureCode.F10_DELIVERED_NOT_ACTIONED, False),  # silent: nothing recorded
        (FailureCode.F06_MANUAL_ROUTING, False),  # silent: nothing recorded
    ],
)
def test_with_errors_finds_every_recorded_error_and_nothing_silent(
    store: PostgresReferralStore, world: World, code: FailureCode, flagged: bool
) -> None:
    for referral_id in sorted(r for r, c in world.labels.items() if c is code)[:3]:
        referral = case_rows(world, referral_id).referral
        day = referral.created_at.astimezone(UTC).date()
        found = store.find_referrals(
            ReferralFilters(
                clinic=referral.recipient_clinic_ref,
                with_errors=True,
                created_from=day,
                created_to=day,
            )
        )
        assert (referral_id in {s.composer_id for s in found}) is flagged, referral_id
