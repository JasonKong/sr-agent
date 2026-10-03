"""Contract tests for deliveries, recipients and return paths against the simulator's truth."""

from collections.abc import Iterator

import psycopg
import pytest

from sr_db.domain import FailureCode, RecipientActionType
from sr_mcp.adapters.postgres import PostgresReferralStore
from sr_simulator.case_rows import case_rows
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.rows import World
from sr_simulator.write import write_world

pytestmark = pytest.mark.integration

WORLD = SimConfig(
    seed=17, practices=8, clinics=14, referrals=600, failure_rate=0.2, distractor_rate=0.1
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


def _cases(world: World, code: FailureCode, n: int = 3) -> list[str]:
    return sorted(r for r, c in world.labels.items() if c is code)[:n]


def _distracted(world: World, kind: str, n: int = 3) -> list[str]:
    return sorted(r for r, k in world.distractors.items() if k == kind)[:n]


def _sample(world: World) -> list[str]:
    picks = [r.id for r in world.referrals if r.id not in world.labels][::60]
    for code in FailureCode:
        picks += _cases(world, code, 2)
    for kind in sorted(set(world.distractors.values())):
        picks += _distracted(world, kind, 2)
    return picks


# --- get_deliveries ----------------------------------------------------------------------------


def test_deliveries_and_attempts_match_the_simulator(
    store: PostgresReferralStore, world: World
) -> None:
    for referral_id in _sample(world):
        rows = case_rows(world, referral_id)
        view = store.deliveries(referral_id)
        assert view is not None, referral_id
        expected = {
            d.id: (d.status.value, d.last_error_code, sorted(
                (a.attempt_no, a.http_status, a.error_code)
                for a in rows.delivery_attempts if a.delivery_id == d.id
            ))
            for d in rows.deliveries
        }  # fmt: skip
        got = {
            d.id: (d.status, d.last_error_code, sorted(
                (a.attempt_no, a.http_status, a.error_code) for a in d.attempts
            ))
            for d in view.deliveries
        }  # fmt: skip
        assert got == expected, referral_id


def _pms(store: PostgresReferralStore, referral_id: str) -> list[tuple[int | None, str | None]]:
    view = store.deliveries(referral_id)
    assert view is not None
    (pms,) = [d for d in view.deliveries if d.channel == "CLINIC_PMS"]
    return [(a.http_status, a.error_code) for a in sorted(pms.attempts, key=lambda a: a.attempt_no)]


def test_f07_fails_without_any_attempt(store: PostgresReferralStore, world: World) -> None:
    for rid in _cases(world, FailureCode.F07_CLINIC_PMS_AUTH_FAILED):
        assert _pms(store, rid) == []


def test_f08_times_out(store: PostgresReferralStore, world: World) -> None:
    for rid in _cases(world, FailureCode.F08_CLINIC_PMS_TIMEOUT):
        attempts = _pms(store, rid)
        assert attempts
        assert all(a == (None, "TIMEOUT") for a in attempts)


def test_f09_is_rejected(store: PostgresReferralStore, world: World) -> None:
    for rid in _cases(world, FailureCode.F09_CLINIC_IMPORT_REJECTED):
        ((status, code),) = _pms(store, rid)
        assert (status, code) in {(409, "PATIENT_MISMATCH"), (422, "PATIENT_INCOMPLETE")}


def test_recovered_auth_shows_401_then_success(store: PostgresReferralStore, world: World) -> None:
    for rid in _distracted(world, "recovered_pms_auth"):
        statuses = [status for status, _ in _pms(store, rid)]
        assert statuses[0] == 401
        assert statuses[1] in {200, 201}


@pytest.mark.parametrize(
    ("code", "words"),
    [
        (FailureCode.F04_HUB_UNAVAILABLE, "no record"),
        (FailureCode.F05_ATTACHMENT_FETCH_FAILED, "not accepted"),
        (FailureCode.F06_MANUAL_ROUTING, "MANUAL"),
    ],
)
def test_empty_deliveries_explain_why(
    store: PostgresReferralStore, world: World, code: FailureCode, words: str
) -> None:
    for rid in _cases(world, code):
        view = store.deliveries(rid)
        assert view is not None
        assert view.deliveries == []
        assert view.note is not None
        assert words in view.note


def test_healthy_deliveries_have_no_note(store: PostgresReferralStore, world: World) -> None:
    healthy = next(
        r
        for r in world.referrals
        if r.id not in world.labels
        and r.id not in world.distractors
        and r.status.value == "ACKNOWLEDGED"
    )
    view = store.deliveries(healthy.id)
    assert view is not None
    assert view.note is None
    assert view.deliveries


# --- get_recipient -----------------------------------------------------------------------------


def test_recipients_match_the_simulator(store: PostgresReferralStore, world: World) -> None:
    for clinic in world.clinics:
        view = store.recipient(clinic.id)
        assert view is not None
        assert view.clinic.has_active_account is clinic.has_active_account
        assert view.routing_mode == ("ELECTRONIC" if clinic.has_active_account else "MANUAL")
        assert {s.id for s in view.specialists} == {
            s.id for s in world.specialists if s.clinic_id == clinic.id
        }
        expected = {
            (i.id, i.vendor.value, i.auth_status.value, i.endpoint_status.value)
            for i in world.clinic_integrations
            if i.clinic_id == clinic.id
        }
        got = {(i.id, i.vendor, i.auth_status, i.endpoint_status) for i in view.integrations}
        assert got == expected
    assert store.recipient("cln_999") is None


def test_broken_integrations_are_visible(store: PostgresReferralStore, world: World) -> None:
    for code, field, value in [
        (FailureCode.F07_CLINIC_PMS_AUTH_FAILED, "auth_status", "EXPIRED"),
        (FailureCode.F08_CLINIC_PMS_TIMEOUT, "endpoint_status", "DEGRADED"),
    ]:
        rid = _cases(world, code, 1)[0]
        clinic = case_rows(world, rid).referral.recipient_clinic_ref
        view = store.recipient(clinic)
        assert view is not None
        assert getattr(view.integrations[0], field) == value


# --- get_return_path -----------------------------------------------------------------------------


def test_return_paths_match_the_simulator(store: PostgresReferralStore, world: World) -> None:
    for referral_id in _sample(world):
        rows = case_rows(world, referral_id)
        view = store.return_path(referral_id)
        assert view is not None
        assert [s.action_id for s in view.steps] == [
            a.id for a in sorted(rows.recipient_actions, key=lambda a: (a.occurred_at, a.id))
        ]
        filings = {i.hub_action_ref: i for i in rows.inbound_responses}
        for step in view.steps:
            assert {c.id for c in step.callbacks} == {
                c.id for c in rows.return_callbacks if c.action_id == step.action_id
            }
            if step.action_id in filings:
                assert step.gp_inbox is not None
                expected = filings[step.action_id].inbox_filing_status.value
                assert step.gp_inbox.inbox_filing_status == expected
            else:
                assert step.gp_inbox is None


def test_f11_responses_were_returned_but_not_filed(
    store: PostgresReferralStore, world: World
) -> None:
    for rid in _cases(world, FailureCode.F11_RESPONSE_NOT_FILED):
        view = store.return_path(rid)
        assert view is not None
        failed = [
            s for s in view.steps if s.gp_inbox and s.gp_inbox.inbox_filing_status == "FAILED"
        ]
        assert failed
        assert failed[-1].action in {"ACKNOWLEDGED", "DECLINED"}
        assert failed[-1].callbacks[0].status == "SUCCEEDED"
        assert failed[-1].gp_inbox is not None
        assert failed[-1].gp_inbox.filing_error


def test_downloads_are_not_returned(store: PostgresReferralStore, world: World) -> None:
    downloaded = next(
        a for a in world.recipient_actions if a.action is RecipientActionType.DOWNLOADED
    )
    hub = next(h for h in world.hub_referrals if h.id == downloaded.hub_referral_id)
    view = store.return_path(hub.id)
    assert view is not None
    step = next(s for s in view.steps if s.action_id == downloaded.id)
    assert step.callbacks == []
    assert step.gp_inbox is None


def test_no_hub_record_means_no_return_path(store: PostgresReferralStore, world: World) -> None:
    rid = _cases(world, FailureCode.F04_HUB_UNAVAILABLE, 1)[0]
    view = store.return_path(rid)
    assert view is not None
    assert view.steps == []
    assert view.note is not None
    assert store.return_path("GR-99998") is None
