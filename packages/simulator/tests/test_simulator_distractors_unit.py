"""Unit tests for distractors (T1.5). No database needed."""

import copy
import dataclasses
from collections import Counter
from collections.abc import Callable
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from sr_db.domain import (
    AuthStatus,
    DeliveryChannel,
    DeliveryStatus,
    FailureCode,
    RecipientActionType,
)
from sr_simulator.case_rows import case_rows, find_evidence
from sr_simulator.config import SimConfig
from sr_simulator.distractors import KINDS
from sr_simulator.generate import generate
from sr_simulator.invariants import healthy_violations
from sr_simulator.rows import World
from sr_simulator.scenario import DISTRACTOR_DIR, load_distractors, parse_distractor

CONFIG = SimConfig(
    seed=7, practices=8, clinics=14, referrals=700, failure_rate=0.2, distractor_rate=0.1
)


@pytest.fixture(scope="module")
def world() -> World:
    return generate(CONFIG)


def _of_kind(world: World, kind: str) -> list[str]:
    return sorted(rid for rid, k in world.distractors.items() if k == kind)


# --- Specs -----------------------------------------------------------------------------------


def test_there_is_one_spec_per_kind() -> None:
    assert set(load_distractors()) == set(KINDS)


def _raw(kind: str) -> dict[str, Any]:
    data = yaml.safe_load((DISTRACTOR_DIR / f"{kind}.yaml").read_text())
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize(
    ("kind", "mutate", "message"),
    [
        ("recovered_pms_auth", lambda d: d.update(kind="made_up"), "unknown distractor kind"),
        ("recovered_pms_auth", lambda d: d.update(true_code="inherit"), "inherit"),
        ("injection_in_note", lambda d: d.update(true_code="F00_NO_FAULT"), "inherit"),
        ("injection_in_note", lambda d: d["params"].update(failure_share=2), "failure_share"),
        ("in_progress", lambda d: d["evidence"].append({"table": "hub.nope"}), "unknown table"),
    ],
)
def test_invalid_specs_are_rejected(
    kind: str, mutate: Callable[[dict[str, Any]], None], message: str
) -> None:
    data = copy.deepcopy(_raw(kind))
    parse_distractor(data)
    mutate(data)

    with pytest.raises(ValidationError, match=message):
        parse_distractor(data)


# --- Assignment ------------------------------------------------------------------------------


def test_every_kind_is_assigned(world: World) -> None:
    assert set(world.distractors.values()) == set(KINDS), Counter(world.distractors.values())


def test_distractor_count_is_close_to_the_target(world: World) -> None:
    target = round(CONFIG.distractor_rate * CONFIG.referrals)
    assert 0.7 * target <= len(world.distractors) <= target


def test_distractors_never_change_the_failure_labels(world: World) -> None:
    without = generate(dataclasses.replace(CONFIG, distractor_rate=0.0))
    assert without.labels == world.labels
    assert without.distractors == {}


def test_only_injections_sit_on_failure_cases(world: World) -> None:
    on_failures = {k for rid, k in world.distractors.items() if rid in world.labels}
    assert on_failures == {"injection_in_note"}
    injections = _of_kind(world, "injection_in_note")
    assert 0 < sum(rid in world.labels for rid in injections) < len(injections)


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_cases_show_the_trap_and_the_truth(world: World, kind: str) -> None:
    spec = load_distractors()[kind]
    cases = _of_kind(world, kind)
    assert cases
    for referral_id in cases:
        rows = case_rows(world, referral_id)
        for evidence in spec.evidence:
            assert find_evidence(rows, evidence), f"{kind} {referral_id}: no {evidence}"


def test_unchanged_f00_distractors_are_still_healthy(world: World) -> None:
    quiet = [
        rid
        for rid, kind in world.distractors.items()
        if rid not in world.labels and not KINDS[kind].changes_data
    ]
    quiet += [rid for rid in _of_kind(world, "injection_in_note") if rid not in world.labels]
    assert healthy_violations(world, quiet) == []


# --- Each kind ---------------------------------------------------------------------------------


def test_recovered_pms_auth_failed_once_then_succeeded(world: World) -> None:
    for rid in _of_kind(world, "recovered_pms_auth"):
        rows = case_rows(world, rid)
        (pms,) = [d for d in rows.deliveries if d.channel is DeliveryChannel.CLINIC_PMS]
        attempts = sorted(
            (a for a in rows.delivery_attempts if a.delivery_id == pms.id),
            key=lambda a: a.attempt_no,
        )
        assert [a.http_status for a in attempts] == [401, 201]
        assert pms.status is DeliveryStatus.SUCCEEDED
        events = [line.event for line in sorted(rows.logs, key=lambda x: x.timestamp)]
        auth = events.index("CLINIC_PMS_AUTH_FAILED")
        assert events.index("TOKEN_REFRESHED") > auth


def test_recovered_hub_send_went_through_after_the_resend(world: World) -> None:
    for rid in _of_kind(world, "recovered_hub_send"):
        rows = case_rows(world, rid)
        sends = sorted(rows.hub_sends, key=lambda s: s.attempt_no)
        assert [s.http_status for s in sends][1] == 201
        assert (sends[0].http_status or 0) >= 500
        (hub,) = rows.hub_referrals
        assert hub.accepted_at is not None
        assert rows.referral.sent_at == sends[1].attempted_at
        assert sends[0].attempted_at < sends[1].attempted_at <= hub.created_at <= hub.accepted_at
        assert hub.accepted_at <= CONFIG.anchor_time


def test_delivered_before_expiry_was_pushed_before_the_token_expired(world: World) -> None:
    for rid in _of_kind(world, "delivered_before_expiry"):
        rows = case_rows(world, rid)
        (integration,) = rows.clinic_integrations
        assert integration.auth_status is AuthStatus.EXPIRED
        assert integration.token_expires_at is not None
        (pms,) = [d for d in rows.deliveries if d.channel is DeliveryChannel.CLINIC_PMS]
        assert pms.updated_at < integration.token_expires_at


def test_in_progress_is_recent_and_unread(world: World) -> None:
    for rid in _of_kind(world, "in_progress"):
        rows = case_rows(world, rid)
        (hub,) = rows.hub_referrals
        assert hub.accepted_at is not None
        assert (CONFIG.anchor_time - hub.accepted_at).total_seconds() <= 24 * 3600
        assert RecipientActionType.VIEWED not in {a.action for a in rows.recipient_actions}


def test_injected_text_only_appears_in_the_note_log_line(world: World) -> None:
    texts = load_distractors()["injection_in_note"].params["texts"]
    assert isinstance(texts, list)
    for _, rows in world.tables():
        for row in rows:
            for value in dataclasses.astuple(row):
                assert not any(isinstance(value, str) and t in value for t in texts)
    carriers = [line for line in world.logs if any(t in line.message for t in texts)]
    assert carriers
    assert {line.event for line in carriers} == {"REFERRAL_NOTE"}
    assert {line.correlation_id for line in carriers} == {
        r.correlation_id
        for r in world.referrals
        if world.distractors.get(r.id) == "injection_in_note"
    }


def test_injected_failure_cases_keep_their_own_evidence(world: World) -> None:
    # Covered for every labelled case by test_cases_match_their_scenario_spec; this checks
    # that injected failure cases exist so that coverage is meaningful.
    injected_failures = [rid for rid in _of_kind(world, "injection_in_note") if rid in world.labels]
    assert injected_failures
    assert all(world.labels[rid] is not FailureCode.F00_NO_FAULT for rid in injected_failures)
