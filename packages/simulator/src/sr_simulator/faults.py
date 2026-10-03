"""Failure injection (DESIGN.md section 3.4).

An *outcome* replaces the healthy result of one leg. Scenario YAML files name an outcome and
give its parameters; this module holds the mechanics. Outcomes are either:

- **referral outcomes**, which change one referral's plan; or
- **clinic outcomes** (F07, F08), which change a clinic integration from a time T onwards.
  Every CLINIC_PMS delivery to that clinic after T fails, so all of those referrals carry
  the label and the data stays consistent.

Fault draws use their own streams (`rng_for(seed, "fault", index)`), so injecting a fault
never changes the healthy draws of any plan.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from random import Random
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sr_db.domain import (
    AuthStatus,
    DeliveryErrorCode,
    EndpointStatus,
    FailureCode,
    RecipientActionType,
)
from sr_simulator.config import SimConfig
from sr_simulator.plan import DOCUMENT_TITLES, ClinicInfo, PmsDeliveryFailure, ReferralPlan
from sr_simulator.rng import rng_for
from sr_simulator.rows import ClinicIntegration
from sr_simulator.timeline import seconds

Leg = Literal["none", "compose", "ingest", "deliver", "respond", "return"]


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class FaultContext:
    config: SimConfig
    no_account_clinics: list[ClinicInfo]

    @property
    def anchor(self) -> datetime:
        return self.config.anchor_time

    def happened(self, moment: datetime) -> bool:
        return moment <= self.config.anchor_time


@dataclass(frozen=True)
class ReferralOutcome:
    leg: Leg
    params: type[Params]
    eligible: Callable[[ReferralPlan, FaultContext], bool]
    apply: Callable[[ReferralPlan, Random, Params, FaultContext], None]


@dataclass(frozen=True)
class ClinicOutcome:
    leg: Leg
    params: type[Params]
    # When the clinic's CLINIC_PMS delivery for this referral happens, if it is eligible.
    pms_time: Callable[[ReferralPlan, FaultContext], datetime | None]
    # Fail one affected referral, given the time T the clinic state changed.
    apply: Callable[[ReferralPlan, Random, Params, datetime], None]
    # The clinic integration as it is at the anchor time, broken since T.
    break_integration: Callable[[ClinicIntegration, datetime], ClinicIntegration]


# --- Compose ---------------------------------------------------------------------------------


class ValidationFailedParams(Params):
    variants: list[Literal["dob_empty", "dob_malformed", "nhi_missing"]] = Field(min_length=1)


def _validation_failed(plan: ReferralPlan, rng: Random, params: Params, ctx: FaultContext) -> None:
    assert isinstance(params, ValidationFailedParams)
    variant = rng.choice(params.variants)
    # Event details name the field and the rule, never the patient's value.
    if variant == "dob_empty":
        plan.date_of_birth_text = None
        plan.validation_error = ("date_of_birth", "date_of_birth is required")
    elif variant == "dob_malformed":
        fmt = rng.choice(["%Y-%m-%d", "%d/%m/%y", "%m-%d-%Y %H:%M"])
        plan.date_of_birth_text = plan.date_of_birth.strftime(fmt)
        plan.validation_error = ("date_of_birth", "date_of_birth is not in DD-MM-YYYY format")
    else:
        plan.synthetic_nhi = None
        plan.validation_error = ("synthetic_nhi", "NHI number is required")


class PdfTooLargeParams(Params):
    limit_bytes: int = Field(gt=0)


def _pdf_too_large(plan: ReferralPlan, rng: Random, params: Params, ctx: FaultContext) -> None:
    assert isinstance(params, PdfTooLargeParams)
    # Three large documents push the PDF past the limit even after compressing it.
    titles = rng.sample(DOCUMENT_TITLES, 3)
    share = params.limit_bytes / 3
    plan.documents = [(t, int(share * rng.uniform(1.35, 2.4))) for t in titles]
    original = sum(size for _, size in plan.documents) + rng.randint(80_000, 200_000)
    compressed = max(int(original * rng.uniform(0.8, 0.92)), params.limit_bytes + 1)
    retry_at = plan.generated_at + seconds(rng, 2, 8)
    plan.pdf_failure = (original, compressed, retry_at, retry_at + seconds(rng, 5, 20))


class SourceDocSkippedParams(Params):
    errors: list[str] = Field(min_length=1)


def _source_doc_skipped(plan: ReferralPlan, rng: Random, params: Params, ctx: FaultContext) -> None:
    assert isinstance(params, SourceDocSkippedParams)
    index = rng.randrange(len(plan.documents))
    plan.skipped_document = (index, rng.choice(params.errors))
    plan.pdf_size_bytes -= plan.documents[index][1]  # the PDF was built without it


# --- Ingest ----------------------------------------------------------------------------------


class HubUnavailableParams(Params):
    statuses: list[int] = Field(min_length=1)


def _hub_unavailable(plan: ReferralPlan, rng: Random, params: Params, ctx: FaultContext) -> None:
    assert isinstance(params, HubUnavailableParams)
    resend = plan.sent_at + timedelta(minutes=rng.randint(55, 95))
    plan.auto_resend_at = resend
    plan.hub_send_failures = [
        (plan.sent_at, rng.choice(params.statuses)),
        (resend + seconds(rng, 0.5, 3), rng.choice(params.statuses)),
    ]
    plan.actions = []


class AttachmentFetchFailedParams(Params):
    errors: list[str] = Field(min_length=1)


def _attachment_fetch_failed(
    plan: ReferralPlan, rng: Random, params: Params, ctx: FaultContext
) -> None:
    assert isinstance(params, AttachmentFetchFailedParams)
    resend = plan.sent_at + timedelta(minutes=rng.randint(55, 95))
    plan.auto_resend_at = resend
    plan.fetch_failure = (rng.choice(params.errors), resend, resend + seconds(rng, 3, 12))
    plan.deliveries = []
    plan.actions = []


class NoParams(Params):
    pass


def _manual_routing(plan: ReferralPlan, rng: Random, params: Params, ctx: FaultContext) -> None:
    clinic = rng.choice(ctx.no_account_clinics)
    plan.clinic = clinic
    plan.specialist = rng.choice(clinic.specialists) if rng.random() < 0.7 else None
    plan.manual_routing = True
    plan.deliveries = []
    plan.actions = []


# --- Deliver ---------------------------------------------------------------------------------


class Rejection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    http_status: int
    error_code: DeliveryErrorCode
    message: str


class ClinicImportRejectedParams(Params):
    rejections: list[Rejection] = Field(min_length=1)


def _keep_only_viewed(plan: ReferralPlan) -> None:
    plan.actions = [a for a in plan.actions if a.kind is RecipientActionType.VIEWED]


def _clinic_import_rejected(
    plan: ReferralPlan, rng: Random, params: Params, ctx: FaultContext
) -> None:
    assert isinstance(params, ClinicImportRejectedParams)
    delivery = plan.pms_delivery()
    assert delivery is not None
    rejection = rng.choice(params.rejections)
    plan.pms_failure = PmsDeliveryFailure(
        error_code=rejection.error_code,
        failed_at=delivery.attempted_at,
        attempts=[
            (delivery.attempted_at, rejection.http_status, rejection.message, delivery.latency_ms)
        ],
    )
    # The specialist sees it in the portal, but it never reaches the patient's file.
    _keep_only_viewed(plan)


class ClinicPmsTimeoutParams(Params):
    attempts: int = Field(ge=1, le=5)
    timeout_seconds: int = Field(gt=0)
    retry_delays_seconds: list[int] = Field(min_length=1)


def _pms_attempt_time(plan: ReferralPlan, ctx: FaultContext) -> datetime | None:
    """When the CLINIC_PMS delivery was first tried, if that is before the anchor time.

    Clinic outcomes affect every such delivery after T, including ones whose retries are
    still running at the anchor time, so the data never shows a broken clinic succeeding.
    """
    delivery = plan.pms_delivery()
    if delivery is None or not ctx.happened(delivery.attempted_at):
        return None
    return delivery.attempted_at


def _clinic_pms_auth_failed(plan: ReferralPlan, rng: Random, params: Params, t: datetime) -> None:
    delivery = plan.pms_delivery()
    assert delivery is not None
    plan.pms_failure = PmsDeliveryFailure(
        DeliveryErrorCode.AUTH_TOKEN_EXPIRED, delivery.attempted_at
    )
    plan.actions = []


def _clinic_pms_timeout(plan: ReferralPlan, rng: Random, params: Params, t: datetime) -> None:
    assert isinstance(params, ClinicPmsTimeoutParams)
    delivery = plan.pms_delivery()
    assert delivery is not None
    at = delivery.attempted_at
    attempts: list[tuple[datetime, int | None, str, int]] = []
    for n in range(params.attempts):
        if n:
            delay = params.retry_delays_seconds[min(n - 1, len(params.retry_delays_seconds) - 1)]
            at += timedelta(seconds=params.timeout_seconds + delay)
        message = f"No response within {params.timeout_seconds} s"
        attempts.append((at, None, message, params.timeout_seconds * 1000))
    final = at + timedelta(seconds=params.timeout_seconds)
    plan.pms_failure = PmsDeliveryFailure(DeliveryErrorCode.TIMEOUT, final, attempts)
    plan.actions = []


def _expire_token(integration: ClinicIntegration, t: datetime) -> ClinicIntegration:
    return replace(integration, auth_status=AuthStatus.EXPIRED, token_expires_at=t, updated_at=t)


def _degrade_endpoint(integration: ClinicIntegration, t: datetime) -> ClinicIntegration:
    return replace(integration, endpoint_status=EndpointStatus.DEGRADED, updated_at=t)


# --- Respond and return ----------------------------------------------------------------------


class NoResponseParams(Params):
    viewed_share: float = Field(ge=0, le=1)
    min_age_days: int = Field(ge=1)


class ResponseFilingFailedParams(Params):
    errors: list[str] = Field(min_length=1)


def _no_response(plan: ReferralPlan, rng: Random, params: Params, ctx: FaultContext) -> None:
    assert isinstance(params, NoResponseParams)
    if rng.random() < params.viewed_share:
        _keep_only_viewed(plan)
    else:
        plan.actions = []


def _final_response_index(plan: ReferralPlan, ctx: FaultContext) -> int | None:
    final = {RecipientActionType.ACKNOWLEDGED, RecipientActionType.DECLINED}
    indexes = [
        k
        for k, a in enumerate(plan.actions)
        if a.kind in final and a.callback_at is not None and ctx.happened(a.callback_at)
    ]
    return indexes[-1] if indexes else None


def _response_filing_failed(
    plan: ReferralPlan, rng: Random, params: Params, ctx: FaultContext
) -> None:
    assert isinstance(params, ResponseFilingFailedParams)
    index = _final_response_index(plan, ctx)
    assert index is not None
    plan.filing_failure = (index, rng.choice(params.errors))


# --- Eligibility -----------------------------------------------------------------------------


def _always(plan: ReferralPlan, ctx: FaultContext) -> bool:
    return True


def _submitted(plan: ReferralPlan, ctx: FaultContext) -> bool:
    return ctx.happened(plan.submitted_at)


def _generated(plan: ReferralPlan, ctx: FaultContext) -> bool:
    return ctx.happened(plan.generated_at + timedelta(seconds=60))


def _viewed_with_documents(plan: ReferralPlan, ctx: FaultContext) -> bool:
    viewed = plan.first(RecipientActionType.VIEWED)
    return bool(plan.documents) and viewed is not None and ctx.happened(viewed.at)


def _resent_by_now(plan: ReferralPlan, ctx: FaultContext) -> bool:
    # The auto-resend runs 55 to 95 minutes after the first send.
    return ctx.happened(plan.sent_at + timedelta(minutes=100))


def _accepted(plan: ReferralPlan, ctx: FaultContext) -> bool:
    return bool(ctx.no_account_clinics) and ctx.happened(plan.accepted_at)


def _pms_attempted(plan: ReferralPlan, ctx: FaultContext) -> bool:
    return _pms_attempt_time(plan, ctx) is not None


def _quiet_for_a_week(plan: ReferralPlan, ctx: FaultContext) -> bool:
    return ctx.happened(plan.accepted_at + timedelta(days=7))


def _responded(plan: ReferralPlan, ctx: FaultContext) -> bool:
    return _final_response_index(plan, ctx) is not None


# --- Registry --------------------------------------------------------------------------------

REFERRAL_OUTCOMES: dict[str, ReferralOutcome] = {
    "validation_failed": ReferralOutcome(
        "compose", ValidationFailedParams, _submitted, _validation_failed
    ),
    "pdf_too_large": ReferralOutcome("compose", PdfTooLargeParams, _generated, _pdf_too_large),
    "source_doc_skipped": ReferralOutcome(
        "compose", SourceDocSkippedParams, _viewed_with_documents, _source_doc_skipped
    ),
    "hub_unavailable": ReferralOutcome(
        "ingest", HubUnavailableParams, _resent_by_now, _hub_unavailable
    ),
    "attachment_fetch_failed": ReferralOutcome(
        "ingest", AttachmentFetchFailedParams, _resent_by_now, _attachment_fetch_failed
    ),
    "manual_routing": ReferralOutcome("ingest", NoParams, _accepted, _manual_routing),
    "clinic_import_rejected": ReferralOutcome(
        "deliver", ClinicImportRejectedParams, _pms_attempted, _clinic_import_rejected
    ),
    "no_response": ReferralOutcome("respond", NoResponseParams, _quiet_for_a_week, _no_response),
    "response_filing_failed": ReferralOutcome(
        "return", ResponseFilingFailedParams, _responded, _response_filing_failed
    ),
}

CLINIC_OUTCOMES: dict[str, ClinicOutcome] = {
    "clinic_pms_auth_failed": ClinicOutcome(
        "deliver", NoParams, _pms_attempt_time, _clinic_pms_auth_failed, _expire_token
    ),
    "clinic_pms_timeout": ClinicOutcome(
        "deliver", ClinicPmsTimeoutParams, _pms_attempt_time, _clinic_pms_timeout, _degrade_endpoint
    ),
}

OUTCOME_LEGS: dict[str, Leg] = {
    **{name: o.leg for name, o in REFERRAL_OUTCOMES.items()},
    **{name: o.leg for name, o in CLINIC_OUTCOMES.items()},
}
OUTCOME_PARAMS: dict[str, type[Params]] = {
    **{name: o.params for name, o in REFERRAL_OUTCOMES.items()},
    **{name: o.params for name, o in CLINIC_OUTCOMES.items()},
}


# --- Assignment ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Injection:
    """What one scenario injects: its code, outcome name and validated parameters."""

    code: FailureCode
    outcome: str
    params: Params


def _quotas(total: int, codes: list[FailureCode], rng: Random) -> dict[FailureCode, int]:
    order = list(codes)
    rng.shuffle(order)
    return {
        code: total // len(order) + (1 if i < total % len(order) else 0)
        for i, code in enumerate(order)
    }


def _assign_clinic_outcome(
    injection: Injection,
    quota: int,
    plans: list[ReferralPlan],
    clinics: list[ClinicInfo],
    claimed_clinics: set[str],
    ctx: FaultContext,
    rng: Random,
) -> dict[str, ClinicIntegration]:
    outcome = CLINIC_OUTCOMES[injection.outcome]
    overrides: dict[str, ClinicIntegration] = {}
    candidates_by_clinic: dict[str, list[tuple[datetime, ReferralPlan]]] = {}
    for plan in plans:
        moment = outcome.pms_time(plan, ctx)
        if moment is not None and plan.label is FailureCode.F00_NO_FAULT:
            candidates_by_clinic.setdefault(plan.clinic.row.id, []).append((moment, plan))

    clinic_order = [c for c in clinics if c.integrations and c.row.id not in claimed_clinics]
    rng.shuffle(clinic_order)
    for clinic in clinic_order:
        if quota <= 0:
            break
        candidates = sorted(candidates_by_clinic.get(clinic.row.id, []), key=lambda c: c[0])
        if not candidates:
            continue
        count = min(rng.randint(1, 4), quota, len(candidates))
        affected = candidates[-count:]
        first_failure = affected[0][0]
        previous = (
            candidates[-count - 1][0]
            if len(candidates) > count
            else first_failure - timedelta(days=1)
        )
        broken_at = previous + (first_failure - previous) * rng.uniform(0.1, 0.9)
        for _, plan in affected:
            plan.label = injection.code
            outcome.apply(
                plan, rng_for(ctx.config.seed, "fault", plan.index), injection.params, broken_at
            )
        integration = clinic.integrations[0]
        overrides[integration.id] = outcome.break_integration(integration, broken_at)
        claimed_clinics.add(clinic.row.id)
        quota -= count
    return overrides


def assign_faults(
    config: SimConfig,
    plans: list[ReferralPlan],
    clinics: list[ClinicInfo],
    injections: Mapping[FailureCode, Injection],
) -> dict[str, ClinicIntegration]:
    """Label about `failure_rate` of the plans with a failure and inject it.

    Codes share the total evenly. Clinic outcomes go first because they claim a clinic and
    possibly several referrals. A code with too few eligible referrals gets fewer cases.
    Returns the clinic integrations to replace, by id.
    """
    rng = rng_for(config.seed, "fault-assignment")
    ctx = FaultContext(config, [c for c in clinics if not c.row.has_active_account])
    codes: list[FailureCode] = [c for c in injections if c is not FailureCode.F00_NO_FAULT]
    quotas = _quotas(round(config.failure_rate * len(plans)), codes, rng)

    overrides: dict[str, ClinicIntegration] = {}
    claimed_clinics: set[str] = set()
    for code in codes:
        injection = injections[code]
        if injection.outcome in CLINIC_OUTCOMES:
            overrides |= _assign_clinic_outcome(
                injection, quotas[code], plans, clinics, claimed_clinics, ctx, rng
            )
    for code in codes:
        injection = injections[code]
        outcome = REFERRAL_OUTCOMES.get(injection.outcome)
        if outcome is None:
            continue
        eligible = [
            p for p in plans if p.label is FailureCode.F00_NO_FAULT and outcome.eligible(p, ctx)
        ]
        for plan in rng.sample(eligible, min(quotas[code], len(eligible))):
            plan.label = code
            outcome.apply(plan, rng_for(config.seed, "fault", plan.index), injection.params, ctx)
    return overrides
