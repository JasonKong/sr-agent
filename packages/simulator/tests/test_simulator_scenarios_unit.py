"""Unit tests for scenario specs and failure injection (T1.3). No database needed."""

import copy
import dataclasses
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from sr_db.domain import (
    AuthStatus,
    DeliveryChannel,
    DeliveryErrorCode,
    DeliveryStatus,
    EndpointStatus,
    FailureCode,
    RecipientActionType,
    ReferralStatus,
    RoutingMode,
)
from sr_simulator.case_rows import CaseRows, case_rows, find_evidence
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.invariants import healthy_violations
from sr_simulator.rows import Delivery, World
from sr_simulator.scenario import SCENARIO_DIR, load_scenarios, parse_scenario

FAULTED = SimConfig(seed=7, practices=8, clinics=14, referrals=700, failure_rate=0.2)
CODES = [code for code in FailureCode if code is not FailureCode.F00_NO_FAULT]


@pytest.fixture(scope="module")
def world() -> World:
    return generate(FAULTED)


def _cases(world: World, code: FailureCode) -> list[str]:
    if code is FailureCode.F00_NO_FAULT:
        return [r.id for r in world.referrals if r.id not in world.labels]
    return sorted(rid for rid, c in world.labels.items() if c is code)


# --- Scenario specs ----------------------------------------------------------------------------


def test_there_is_one_scenario_per_failure_code() -> None:
    assert set(load_scenarios()) == set(FailureCode)


def _raw(code: str) -> dict[str, Any]:
    data = yaml.safe_load((SCENARIO_DIR / f"{code}.yaml").read_text())
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["inject"].update(outcome="made_up"), "unknown outcome"),
        (lambda d: d.update(leg="compose"), "belongs to leg"),
        (lambda d: d["evidence"].append({"table": "hub.nope"}), "unknown table"),
        (
            lambda d: d["evidence"].append({"table": "hub.delivery", "where": {"colour": 1}}),
            "colour",
        ),
        (lambda d: d["inject"]["params"].update(attempts=9), "attempts"),
        (lambda d: d.update(surprise=True), "surprise"),
        (lambda d: d["expect"].update(hub="maybe"), "hub"),
    ],
)
def test_invalid_specs_are_rejected_when_loading(
    mutate: Callable[[dict[str, Any]], None], message: str
) -> None:
    data = copy.deepcopy(_raw("F08_CLINIC_PMS_TIMEOUT"))
    parse_scenario(data)  # the real file is valid
    mutate(data)

    with pytest.raises(ValidationError, match=message):
        parse_scenario(data)


def test_only_f00_may_omit_the_outcome() -> None:
    data = _raw("F07_CLINIC_PMS_AUTH_FAILED")
    data["inject"] = {}

    with pytest.raises(ValidationError, match="must name an outcome"):
        parse_scenario(data)


# --- Injection -----------------------------------------------------------------------------


def test_injection_is_reproducible(world: World) -> None:
    assert generate(FAULTED) == world


def test_failure_rate_is_close_to_the_target(world: World) -> None:
    target = round(FAULTED.failure_rate * FAULTED.referrals)
    assert 0.85 * target <= len(world.labels) <= target


def test_every_failure_code_is_injected(world: World) -> None:
    counts = Counter(world.labels.values())
    assert set(counts) == set(CODES), counts


def test_no_failures_when_the_rate_is_zero() -> None:
    assert generate(dataclasses.replace(FAULTED, failure_rate=0.0)).labels == {}


def test_injection_does_not_change_who_referred_whom_or_when(world: World) -> None:
    healthy = generate(dataclasses.replace(FAULTED, failure_rate=0.0))
    for before, after in zip(healthy.referrals, world.referrals, strict=True):
        assert (before.id, before.created_at, before.gp_user_id, before.correlation_id) == (
            after.id,
            after.created_at,
            after.gp_user_id,
            after.correlation_id,
        )


def test_unlabelled_referrals_stay_healthy(world: World) -> None:
    assert healthy_violations(world, _cases(world, FailureCode.F00_NO_FAULT)) == []


@pytest.mark.parametrize("code", list(FailureCode), ids=lambda c: c.value[:3])
def test_cases_match_their_scenario_spec(world: World, code: FailureCode) -> None:
    spec = load_scenarios()[code]
    cases = _cases(world, code)
    assert cases
    for referral_id in cases:
        rows = case_rows(world, referral_id)
        referral, hub = rows.referral, rows.hub_referrals
        context = f"{code.value} {referral_id}"
        assert referral.status in spec.expect.composer_status, context
        if spec.expect.hub == "absent":
            assert hub == [], context
        elif spec.expect.hub == "present":
            assert len(hub) == 1, context
            if spec.expect.hub_status:
                assert hub[0].status in spec.expect.hub_status, context
        if code is FailureCode.F00_NO_FAULT and referral.status is ReferralStatus.DRAFT:
            continue
        for evidence in spec.evidence:
            assert find_evidence(rows, evidence), f"{context}: no {evidence}"


# --- What must NOT be there (hard to say in YAML) --------------------------------------------


def _pms_deliveries(rows: CaseRows) -> list[Delivery]:
    return [d for d in rows.deliveries if d.channel is DeliveryChannel.CLINIC_PMS]


def test_f04_hub_has_no_record_and_both_sends_failed(world: World) -> None:
    for rid in _cases(world, FailureCode.F04_HUB_UNAVAILABLE):
        rows = case_rows(world, rid)
        assert [s.attempt_no for s in rows.hub_sends] == [1, 2]
        assert all((s.http_status or 0) >= 500 for s in rows.hub_sends)
        assert rows.referral.hub_referral_id is None


def test_f05_nothing_is_delivered(world: World) -> None:
    for rid in _cases(world, FailureCode.F05_ATTACHMENT_FETCH_FAILED):
        rows = case_rows(world, rid)
        assert rows.hub_referrals[0].accepted_at is None
        assert rows.deliveries == []
        assert [s.http_status for s in rows.hub_sends] == [201, 201]


def test_f06_manual_referrals_are_never_delivered(world: World) -> None:
    for rid in _cases(world, FailureCode.F06_MANUAL_ROUTING):
        rows = case_rows(world, rid)
        assert rows.hub_referrals[0].routing_mode is RoutingMode.MANUAL
        assert rows.deliveries == []
        assert rows.recipient_actions == []


def test_f07_auth_failure_happens_before_any_attempt(world: World) -> None:
    for rid in _cases(world, FailureCode.F07_CLINIC_PMS_AUTH_FAILED):
        rows = case_rows(world, rid)
        (pms,) = _pms_deliveries(rows)
        assert not [a for a in rows.delivery_attempts if a.delivery_id == pms.id]
        others = [d for d in rows.deliveries if d is not pms]
        assert all(d.status is DeliveryStatus.SUCCEEDED for d in others)


def test_f10_has_no_response_after_a_week(world: World) -> None:
    for rid in _cases(world, FailureCode.F10_DELIVERED_NOT_ACTIONED):
        actions = {a.action for a in case_rows(world, rid).recipient_actions}
        assert actions <= {RecipientActionType.VIEWED}


def test_patient_values_never_appear_in_event_details(world: World) -> None:
    patients = {p.id: p for p in world.patients}
    by_referral = {r.id: patients[r.patient_id] for r in world.referrals}
    for event in world.referral_events:
        p = by_referral[event.referral_id]
        for value in (p.synthetic_nhi, p.date_of_birth, p.given_name, p.family_name):
            assert not value or value not in (event.detail or ""), event


# --- Clinic outcomes are consistent across every referral to the clinic -----------------------


def test_broken_clinic_integrations_fail_every_delivery_after_they_broke(world: World) -> None:
    first_try: dict[str, datetime] = {}
    for a in world.delivery_attempts:
        first_try.setdefault(a.delivery_id, a.attempted_at)
    reasons = {
        AuthStatus.EXPIRED: DeliveryErrorCode.AUTH_TOKEN_EXPIRED,
        EndpointStatus.DEGRADED: DeliveryErrorCode.TIMEOUT,
    }
    broken = [
        (i, reasons[i.auth_status if i.auth_status is AuthStatus.EXPIRED else i.endpoint_status])
        for i in world.clinic_integrations
        if i.auth_status is AuthStatus.EXPIRED or i.endpoint_status is EndpointStatus.DEGRADED
    ]
    assert broken
    for integration, reason in broken:
        since = integration.updated_at
        for d in world.deliveries:
            if d.target_ref != integration.id:
                continue
            tried_at = first_try.get(d.id, d.updated_at)
            if tried_at < since:
                # Earlier deliveries may fail for their own reasons (F09), never this one.
                assert d.last_error_code != reason, d
            else:
                assert d.status is not DeliveryStatus.SUCCEEDED, d
                assert d.last_error_code == reason, d
