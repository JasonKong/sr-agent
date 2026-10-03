"""Distractors (DESIGN.md section 3.5): data that looks like a failure, or misleads, while the
true code stays the same.

Distractors are assigned after faults, from their own streams, so they never change which
referrals carry which failure. A referral gets at most one distractor. Kinds either:

- **change the data**, such as a failure that recovered (`recovered_pms_auth`,
  `recovered_hub_send`) or a note with a prompt injection (`injection_in_note`); or
- **only tag** a referral that is already confusing (`delivered_before_expiry`,
  `in_progress`), so evaluation can pick it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from random import Random
from typing import Literal

from pydantic import Field

from sr_db.domain import AuthStatus, FailureCode, RecipientActionType
from sr_simulator.config import SimConfig
from sr_simulator.faults import NoParams, Params
from sr_simulator.plan import ReferralPlan
from sr_simulator.rng import rng_for
from sr_simulator.rows import ClinicIntegration
from sr_simulator.timeline import seconds


@dataclass(frozen=True)
class DistractorContext:
    config: SimConfig
    # Clinic id -> when its PMS token expired (clinics broken by F07).
    token_expired_at: dict[str, datetime]
    # Clinic id -> when its integration broke (F07 or F08). Every CLINIC_PMS delivery after
    # that must fail, so distractors that move a delivery later must not cross it.
    broken_since: dict[str, datetime]

    def stays_before_breakage(self, plan: ReferralPlan, later_by: timedelta) -> bool:
        broken = self.broken_since.get(plan.clinic.row.id)
        delivery = plan.pms_delivery()
        return broken is None or delivery is None or delivery.attempted_at + later_by < broken

    def happened(self, moment: datetime) -> bool:
        return moment <= self.config.anchor_time


@dataclass(frozen=True)
class DistractorKind:
    params: type[Params]
    changes_data: bool
    # Which referrals it may sit on: healthy (F00) ones, failure cases, or any.
    on: Literal["healthy", "failures", "any"]
    eligible: Callable[[ReferralPlan, DistractorContext], bool]
    apply: Callable[[ReferralPlan, Random, Params, DistractorContext], None]


# --- recovered_pms_auth -------------------------------------------------------------------------


class RecoveredPmsAuthParams(Params):
    retry_delay_seconds: list[float] = Field(min_length=2, max_length=2)


def _pms_delivered_with_room(plan: ReferralPlan, ctx: DistractorContext) -> bool:
    delivery = plan.pms_delivery()
    return (
        delivery is not None
        and ctx.happened(delivery.attempted_at + timedelta(seconds=30))
        and ctx.stays_before_breakage(plan, timedelta(seconds=30))
    )


def _recovered_pms_auth(
    plan: ReferralPlan, rng: Random, params: Params, ctx: DistractorContext
) -> None:
    assert isinstance(params, RecoveredPmsAuthParams)
    delivery = plan.pms_delivery()
    assert delivery is not None
    low, high = params.retry_delay_seconds
    plan.pms_auth_refreshed_at = delivery.attempted_at + seconds(rng, low, high)


# --- recovered_hub_send ---------------------------------------------------------------------------


class RecoveredHubSendParams(Params):
    statuses: list[int] = Field(min_length=1)
    resend_after_minutes: list[int] = Field(min_length=2, max_length=2)


def _resent_and_accepted_by_now(plan: ReferralPlan, ctx: DistractorContext) -> bool:
    # Must be accepted by the anchor time; otherwise it would look exactly like F04.
    later_by = timedelta(minutes=100)
    return ctx.happened(plan.accepted_at + later_by) and ctx.stays_before_breakage(plan, later_by)


def _recovered_hub_send(
    plan: ReferralPlan, rng: Random, params: Params, ctx: DistractorContext
) -> None:
    assert isinstance(params, RecoveredHubSendParams)
    low, high = params.resend_after_minutes
    first_try = plan.sent_at
    delay = timedelta(minutes=rng.randint(low, high))
    plan.hub_send_recovered = (first_try, rng.choice(params.statuses))
    plan.auto_resend_at = first_try + delay - seconds(rng, 0.5, 2)
    plan.delay_handoff(delay)


# --- delivered_before_expiry and in_progress (tags only) ---------------------------------------


def _delivered_before_expiry(plan: ReferralPlan, ctx: DistractorContext) -> bool:
    expired_at = ctx.token_expired_at.get(plan.clinic.row.id)
    delivery = plan.pms_delivery()
    return expired_at is not None and delivery is not None and delivery.attempted_at < expired_at


def _recent_and_unread(plan: ReferralPlan, ctx: DistractorContext) -> bool:
    """Accepted in the last 24 hours and not viewed yet: normal, but "not received yet"."""
    viewed = plan.first(RecipientActionType.VIEWED)
    anchor = ctx.config.anchor_time
    return (
        ctx.happened(plan.accepted_at)
        and anchor - plan.accepted_at <= timedelta(hours=24)
        and (viewed is None or not ctx.happened(viewed.at))
    )


def _tag_only(plan: ReferralPlan, rng: Random, params: Params, ctx: DistractorContext) -> None:
    return None


# --- misleading_ticket ------------------------------------------------------------------------

# Failure codes whose cause sits with the recipient clinic, and the cause a misleading past
# ticket for the same clinic describes instead.
MISLEADING_CODE = {
    FailureCode.F07_CLINIC_PMS_AUTH_FAILED: FailureCode.F08_CLINIC_PMS_TIMEOUT,
    FailureCode.F08_CLINIC_PMS_TIMEOUT: FailureCode.F07_CLINIC_PMS_AUTH_FAILED,
    FailureCode.F09_CLINIC_IMPORT_REJECTED: FailureCode.F07_CLINIC_PMS_AUTH_FAILED,
}


def _clinic_specific_failure(plan: ReferralPlan, ctx: DistractorContext) -> bool:
    return plan.label in MISLEADING_CODE


# --- injection_in_note ------------------------------------------------------------------------


class InjectionParams(Params):
    texts: list[str] = Field(min_length=1)
    failure_share: float = Field(ge=0, le=1)


def _submitted(plan: ReferralPlan, ctx: DistractorContext) -> bool:
    return ctx.happened(plan.submitted_at)


def _injection_in_note(
    plan: ReferralPlan, rng: Random, params: Params, ctx: DistractorContext
) -> None:
    assert isinstance(params, InjectionParams)
    plan.note = rng.choice(params.texts)


KINDS: dict[str, DistractorKind] = {
    "recovered_pms_auth": DistractorKind(
        RecoveredPmsAuthParams, True, "healthy", _pms_delivered_with_room, _recovered_pms_auth
    ),
    "recovered_hub_send": DistractorKind(
        RecoveredHubSendParams, True, "healthy", _resent_and_accepted_by_now, _recovered_hub_send
    ),
    "delivered_before_expiry": DistractorKind(
        NoParams, False, "healthy", _delivered_before_expiry, _tag_only
    ),
    "in_progress": DistractorKind(NoParams, False, "healthy", _recent_and_unread, _tag_only),
    "injection_in_note": DistractorKind(
        InjectionParams, True, "any", _submitted, _injection_in_note
    ),
    # Tag only here; tickets.py writes the misleading past ticket for the case's clinic.
    "misleading_ticket": DistractorKind(
        NoParams, False, "failures", _clinic_specific_failure, _tag_only
    ),
}


def assign_distractors(
    config: SimConfig,
    plans: list[ReferralPlan],
    overrides: Mapping[str, ClinicIntegration],
    params: Mapping[str, Params],
) -> None:
    """Give about `distractor_rate` of the plans one distractor, shared evenly across kinds."""
    rng = rng_for(config.seed, "distractor-assignment")
    expired = {
        i.clinic_id: i.token_expires_at
        for i in overrides.values()
        if i.auth_status is AuthStatus.EXPIRED and i.token_expires_at is not None
    }
    broken = {
        i.clinic_id: i.token_expires_at if i.auth_status is AuthStatus.EXPIRED else i.updated_at
        for i in overrides.values()
    }
    ctx = DistractorContext(config, expired, {k: v for k, v in broken.items() if v is not None})
    kinds = [kind for kind in KINDS if kind in params]
    total = round(config.distractor_rate * len(plans))
    order = list(kinds)
    rng.shuffle(order)
    quotas = {
        k: total // len(order) + (1 if i < total % len(order) else 0) for i, k in enumerate(order)
    }

    for name in kinds:
        kind, kind_params = KINDS[name], params[name]
        free = [p for p in plans if p.distractor is None and kind.eligible(p, ctx)]
        if name == "delivered_before_expiry":
            # The most confusing one per clinic: the last delivery before the token expired.
            by_clinic: dict[str, ReferralPlan] = {}
            for plan in free:
                if plan.label is FailureCode.F00_NO_FAULT:
                    by_clinic[plan.clinic.row.id] = plan
            chosen = list(by_clinic.values())[: quotas[name]]
        elif kind.on == "failures":
            failures = [p for p in free if p.label is not FailureCode.F00_NO_FAULT]
            chosen = rng.sample(failures, min(quotas[name], len(failures)))
        elif kind.on == "any":
            assert isinstance(kind_params, InjectionParams)
            on_failures = round(quotas[name] * kind_params.failure_share)
            failures = [p for p in free if p.label is not FailureCode.F00_NO_FAULT]
            healthy = [p for p in free if p.label is FailureCode.F00_NO_FAULT]
            chosen = rng.sample(failures, min(on_failures, len(failures)))
            chosen += rng.sample(healthy, min(quotas[name] - len(chosen), len(healthy)))
        else:
            healthy = [p for p in free if p.label is FailureCode.F00_NO_FAULT]
            chosen = rng.sample(healthy, min(quotas[name], len(healthy)))
        for plan in chosen:
            plan.distractor = name
            kind.apply(plan, rng_for(config.seed, "distractor", plan.index), kind_params, ctx)
