"""Participants, and the plan for one referral (DESIGN.md section 3.2).

A plan holds every timing and choice for a referral, drawn up front from the referral's own
stream with one method per leg. Fault fields default to "healthy"; `faults.py` sets them to
replace the outcome of one leg, and `generate.py` emits rows from the result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from random import Random

from sr_db.domain import DeliveryChannel, DeliveryErrorCode, FailureCode, RecipientActionType
from sr_simulator.rows import Clinic, ClinicIntegration, GpPractice, GpUser, Specialist
from sr_simulator.timeline import after_business_days, seconds

DOCUMENT_TITLES = [
    "Recent blood results", "Discharge summary", "X-ray report", "Ultrasound report",
    "Previous specialist letter", "ECG", "Medication list", "Clinical photo",
]  # fmt: skip


@dataclass(frozen=True)
class Practice:
    row: GpPractice
    users: list[GpUser]


@dataclass(frozen=True)
class ClinicInfo:
    row: Clinic
    specialists: list[Specialist]
    integrations: list[ClinicIntegration]


@dataclass
class PlannedAction:
    """A recipient action, with its return-leg callback time (None for DOWNLOADED)."""

    kind: RecipientActionType
    at: datetime
    callback_at: datetime | None = None


@dataclass
class PlannedDelivery:
    channel: DeliveryChannel
    target: str
    attempted_at: datetime
    latency_ms: int
    http_status: int


@dataclass
class PmsDeliveryFailure:
    """How the CLINIC_PMS delivery failed (F07, F08, F09)."""

    error_code: DeliveryErrorCode
    failed_at: datetime
    # (attempted_at, http_status, error_message, latency_ms); empty when the failure
    # happens before any request is sent, as with authentication (F07).
    attempts: list[tuple[datetime, int | None, str, int]] = field(default_factory=list)


@dataclass
class ReferralPlan:
    """Every timing and choice for one referral, plus any injected fault."""

    index: int
    rng: Random
    created_at: datetime
    practice: Practice
    gp_user: GpUser
    clinic: ClinicInfo
    specialist: Specialist | None
    correlation_id: str
    patient_names: tuple[str, str]
    date_of_birth: date
    number: int = field(init=False)  # the GR- number, assigned in creation order
    # Leg 1, compose
    submitted_at: datetime = field(init=False)
    documents: list[tuple[str, int]] = field(init=False)
    generated_at: datetime = field(init=False)
    pdf_size_bytes: int = field(init=False)
    outbox_at: datetime = field(init=False)
    # Leg 2, ingest
    sent_at: datetime = field(init=False)
    hub_created_at: datetime = field(init=False)
    file_fetched_at: datetime = field(init=False)
    accepted_at: datetime = field(init=False)
    accept_notified_at: datetime = field(init=False)
    # Legs 3 to 5
    deliveries: list[PlannedDelivery] = field(init=False)
    actions: list[PlannedAction] = field(init=False)

    # Patient fields as the GP PMS sent them (F01 can blank or garble them).
    date_of_birth_text: str | None = field(init=False)
    synthetic_nhi: str | None = field(init=False)

    # Injected fault; each field replaces the healthy outcome of one leg.
    label: FailureCode = field(default=FailureCode.F00_NO_FAULT, init=False)
    validation_error: tuple[str, str] | None = field(default=None, init=False)
    pdf_failure: tuple[int, int, datetime, datetime] | None = field(default=None, init=False)
    skipped_document: tuple[int, str] | None = field(default=None, init=False)
    hub_send_failures: list[tuple[datetime, int]] = field(default_factory=list, init=False)
    auto_resend_at: datetime | None = field(default=None, init=False)
    fetch_failure: tuple[str, datetime, datetime] | None = field(default=None, init=False)
    manual_routing: bool = field(default=False, init=False)
    pms_failure: PmsDeliveryFailure | None = field(default=None, init=False)
    filing_failure: tuple[int, str] | None = field(default=None, init=False)

    # Distractor (DESIGN.md section 3.5): looks like a failure, or misleads, but the label stays.
    distractor: str | None = field(default=None, init=False)
    pms_auth_refreshed_at: datetime | None = field(default=None, init=False)
    hub_send_recovered: tuple[datetime, int] | None = field(default=None, init=False)
    note: str | None = field(default=None, init=False)

    def plan(self) -> None:
        self.date_of_birth_text = self.date_of_birth.strftime("%d-%m-%Y")
        self.synthetic_nhi = f"SIM{self.index:04d}"
        self.compose()
        self.ingest()
        self.deliver()
        self.respond()
        self.return_leg()

    def compose(self) -> None:
        rng = self.rng
        self.submitted_at = self.created_at + seconds(rng, 120, 1200)
        count = rng.choices([0, 1, 2, 3, 4], weights=[15, 30, 30, 15, 10])[0]
        titles = rng.sample(DOCUMENT_TITLES, count)
        self.documents = [
            (title, int(math.exp(rng.uniform(math.log(50_000), math.log(2_000_000)))))
            for title in titles
        ]
        self.generated_at = self.submitted_at + seconds(rng, 5, 40)
        self.pdf_size_bytes = sum(size for _, size in self.documents) + rng.randint(80_000, 200_000)
        self.outbox_at = self.generated_at + seconds(rng, 1, 5)

    def ingest(self) -> None:
        rng = self.rng
        self.sent_at = self.generated_at + seconds(rng, 1, 3)
        self.hub_created_at = self.sent_at + seconds(rng, 0.2, 1.5)
        self.file_fetched_at = self.hub_created_at + seconds(rng, 2, 10)
        self.accepted_at = self.file_fetched_at + seconds(rng, 0, 2)
        self.accept_notified_at = self.accepted_at + seconds(rng, 0.1, 2)

    def deliver(self) -> None:
        rng = self.rng
        targets: list[tuple[DeliveryChannel, str, int]] = [
            (DeliveryChannel.EMAIL, "clinic-email", 202),
            (DeliveryChannel.PORTAL, "portal", 200),
        ]
        targets += [(DeliveryChannel.CLINIC_PMS, i.id, 201) for i in self.clinic.integrations]
        self.deliveries = [
            PlannedDelivery(
                channel,
                target,
                self.accepted_at + seconds(rng, 1, 60),
                rng.randint(150, 3000),
                status,
            )
            for channel, target, status in targets
        ]

    def respond(self) -> None:
        rng = self.rng
        delivered = max(d.attempted_at for d in self.deliveries)
        viewed = after_business_days(rng, delivered, 3)
        actions = [PlannedAction(RecipientActionType.VIEWED, viewed)]
        if rng.random() < 0.5:
            actions.append(
                PlannedAction(RecipientActionType.DOWNLOADED, viewed + seconds(rng, 60, 1800))
            )
        roll = rng.random()
        responded = after_business_days(rng, viewed, 5)
        if roll < 0.85:
            actions.append(PlannedAction(RecipientActionType.ACKNOWLEDGED, responded))
        elif roll < 0.95:
            actions.append(PlannedAction(RecipientActionType.DECLINED, responded))
        else:
            actions.append(PlannedAction(RecipientActionType.MESSAGE, responded))
            later = after_business_days(rng, responded, 2)
            actions.append(PlannedAction(RecipientActionType.ACKNOWLEDGED, later))
        self.actions = sorted(actions, key=lambda a: a.at)

    def return_leg(self) -> None:
        rng = self.rng
        for action in self.actions:
            if action.kind is not RecipientActionType.DOWNLOADED:
                action.callback_at = action.at + seconds(rng, 1, 10)

    def delay_handoff(self, delay: timedelta) -> None:
        """Move the successful hand-off, and everything after it, later by `delay`."""
        self.sent_at += delay
        self.hub_created_at += delay
        self.file_fetched_at += delay
        self.accepted_at += delay
        self.accept_notified_at += delay
        for delivery in self.deliveries:
            delivery.attempted_at += delay
        for action in self.actions:
            action.at += delay
            if action.callback_at is not None:
                action.callback_at += delay

    # --- Helpers used by fault eligibility ---------------------------------------------------

    def pms_delivery(self) -> PlannedDelivery | None:
        return next((d for d in self.deliveries if d.channel is DeliveryChannel.CLINIC_PMS), None)

    def first(self, kind: RecipientActionType) -> PlannedAction | None:
        return next((a for a in self.actions if a.kind is kind), None)
