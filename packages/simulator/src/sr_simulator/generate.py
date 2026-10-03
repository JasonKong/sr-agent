"""Generate a healthy world: participants, and referrals that travel all four legs.

Each referral is built in two steps:

1. **Plan.** Draw every random value for every leg, in a fixed order, from the referral's
   own stream. The result is a timeline of when each step would happen.
2. **Emit.** Turn the plan into rows, leaving out anything scheduled after the anchor time.
   It has not happened yet, which is why recent referrals are still in progress.

The plan has one method per leg (DESIGN.md section 3.2). Failure injection (T1.3) replaces
a single leg; draws never depend on the anchor cut-off, so the other legs stay identical.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from itertools import product
from random import Random

from faker import Faker

from sr_db.domain import (
    ActiveStatus,
    AuthStatus,
    CallbackStatus,
    ClinicPmsVendor,
    DeliveryChannel,
    DeliveryStatus,
    EndpointStatus,
    ErrorLevel,
    EventLevel,
    FetchStatus,
    GpPmsVendor,
    GpUserRole,
    HubFileKind,
    HubReferralStatus,
    InboxFilingStatus,
    RecipientActionType,
    ReferralStatus,
    ResponseType,
    RoutingMode,
)
from sr_simulator.config import NZ, SimConfig
from sr_simulator.rng import derive_seed, rng_for
from sr_simulator.rows import (
    Clinic,
    ClinicIntegration,
    Delivery,
    DeliveryAttempt,
    GpPractice,
    GpUser,
    HubFile,
    HubReferral,
    HubSend,
    InboundResponse,
    PatientSnapshot,
    PmsConnection,
    RecipientAction,
    Referral,
    ReferralEvent,
    ReturnCallback,
    SourceDocument,
    Specialist,
    World,
)

# Fictional names. Vendor words (Kauri, Harbour, Summit, Tasman, Ridgeline) are avoided.
PRACTICE_WORDS = [
    "Totara Street", "Rimu Road", "Matai", "Miro", "Kahikatea", "Pohutukawa", "Rata", "Karaka",
    "Ngaio", "Titoki", "Puriri", "Manuka", "Kanuka", "Horoeka", "Tawa", "Hinau",
]  # fmt: skip
PRACTICE_SUFFIXES = ["Medical Centre", "Family Health", "Health Centre", "Medical Practice"]
CLINIC_WORDS = [
    "Kowhai", "Tui", "Kereru", "Piwakawaka", "Kotare", "Ruru", "Tieke", "Hihi",
    "Pukeko", "Takahe", "Kaka", "Kea", "Weka", "Toroa", "Kotuku", "Matuku",
]  # fmt: skip
SPECIALTIES = [
    ("ORTHOPAEDICS", "Orthopaedics"),
    ("CARDIOLOGY", "Cardiology"),
    ("DERMATOLOGY", "Skin Clinic"),
    ("ENT", "ENT Specialists"),
    ("GASTROENTEROLOGY", "Gastroenterology"),
    ("OPHTHALMOLOGY", "Eye Specialists"),
    ("GENERAL_SURGERY", "Surgical"),
    ("GYNAECOLOGY", "Women's Health"),
    ("UROLOGY", "Urology"),
    ("PAEDIATRICS", "Children's Specialists"),
]
DOCUMENT_TITLES = [
    "Recent blood results", "Discharge summary", "X-ray report", "Ultrasound report",
    "Previous specialist letter", "ECG", "Medication list", "Clinical photo",
]  # fmt: skip

GP_VENDOR_WEIGHTS = {
    GpPmsVendor.KAURI_PMS: 50,
    GpPmsVendor.HARBOUR_PMS: 30,
    GpPmsVendor.SUMMIT_PMS: 20,
}
CLINIC_VENDOR_WEIGHTS = {
    ClinicPmsVendor.KAURI_PMS: 40,
    ClinicPmsVendor.TASMAN_CLINIC: 35,
    ClinicPmsVendor.RIDGELINE: 25,
}
ACTIVE_ACCOUNT_SHARE = 0.9
INTEGRATION_SHARE = 0.6
NAMED_SPECIALIST_SHARE = 0.7


# --- Time helpers ---------------------------------------------------------------------------


def _seconds(rng: Random, low: float, high: float) -> timedelta:
    return timedelta(seconds=rng.uniform(low, high))


def _business_moment(rng: Random, day: date) -> datetime:
    """A random time between 08:00 and 17:30 local time on `day`."""
    minute = rng.randint(8 * 60, 17 * 60 + 29)
    return datetime(
        day.year, day.month, day.day, minute // 60, minute % 60, rng.randint(0, 59), tzinfo=NZ
    )


def _next_business_day(day: date) -> date:
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _after_business_days(rng: Random, start: datetime, max_days: int) -> datetime:
    """Later the same day, or during business hours up to `max_days` business days later."""
    days = rng.randint(0, max_days)
    if days == 0:
        return start + timedelta(minutes=rng.randint(5, 240))
    day = start.astimezone(NZ).date()
    for _ in range(days):
        day = _next_business_day(day)
    return _business_moment(rng, day)


def _created_at(rng: Random, config: SimConfig) -> datetime:
    """A business-hours moment within the history window, before the anchor time."""
    day = config.anchor_time.astimezone(NZ).date() - timedelta(
        days=rng.randrange(config.history_days)
    )
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    moment = _business_moment(rng, day)
    if moment >= config.anchor_time:
        moment = config.anchor_time - timedelta(minutes=rng.randint(1, 90))
    return moment


def _weighted[T](rng: Random, weights: dict[T, int]) -> T:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _person(fake: Faker, title: str = "") -> str:
    return f"{title}{fake.first_name()} {fake.last_name()}"


# --- Participants ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Practice:
    row: GpPractice
    users: list[GpUser]


@dataclass(frozen=True)
class _Clinic:
    row: Clinic
    specialists: list[Specialist]
    integrations: list[ClinicIntegration]


def _practices(config: SimConfig, fake: Faker, world: World) -> list[_Practice]:
    seed, anchor = config.seed, config.anchor_time
    names = list(product(PRACTICE_WORDS, PRACTICE_SUFFIXES))
    rng_for(seed, "practice-names").shuffle(names)
    practices: list[_Practice] = []
    user_no = 0
    for n in range(1, config.practices + 1):
        rng = rng_for(seed, "practice", n)
        fake.seed_instance(derive_seed(seed, "practice-people", n))
        word, suffix = names[n - 1]
        practice = GpPractice(
            id=f"prc_{n}",
            name=f"{word} {suffix}",
            pms_vendor=_weighted(rng, GP_VENDOR_WEIGHTS),
            status=ActiveStatus.ACTIVE,
            created_at=anchor - timedelta(days=rng.randint(2 * 365, 6 * 365)),
        )
        users = []
        for _ in range(rng.randint(2, 8)):
            user_no += 1
            role = GpUserRole.GP if rng.random() < 0.8 else GpUserRole.NURSE_PRACTITIONER
            title = "Dr " if role is GpUserRole.GP else ""
            users.append(GpUser(f"gpu_{user_no}", practice.id, _person(fake, title), role))
        connection = PmsConnection(
            id=f"pmc_{n}",
            practice_id=practice.id,
            auth_status=AuthStatus.VALID,
            token_expires_at=anchor + timedelta(days=rng.randint(30, 180)),
            updated_at=anchor - timedelta(days=rng.randint(1, 20), minutes=rng.randint(0, 600)),
        )
        world.gp_practices.append(practice)
        world.gp_users.extend(users)
        world.pms_connections.append(connection)
        practices.append(_Practice(practice, users))
    return practices


def _clinics(config: SimConfig, fake: Faker, world: World) -> list[_Clinic]:
    seed, anchor = config.seed, config.anchor_time
    names = list(product(CLINIC_WORDS, SPECIALTIES))
    rng_for(seed, "clinic-names").shuffle(names)
    clinics: list[_Clinic] = []
    specialist_no = integration_no = 0
    for n in range(1, config.clinics + 1):
        rng = rng_for(seed, "clinic", n)
        fake.seed_instance(derive_seed(seed, "clinic-people", n))
        word, (group, label) = names[n - 1]
        clinic = Clinic(
            id=f"cln_{n}",
            name=f"{word} {label}",
            specialty_group=group,
            status=ActiveStatus.ACTIVE,
            has_active_account=rng.random() < ACTIVE_ACCOUNT_SHARE,
            created_at=anchor - timedelta(days=rng.randint(365, 4 * 365)),
        )
        specialists = []
        for _ in range(rng.randint(1, 6)):
            specialist_no += 1
            specialists.append(
                Specialist(
                    f"spc_{specialist_no}", clinic.id, _person(fake, "Dr "), ActiveStatus.ACTIVE
                )
            )
        integrations = []
        if clinic.has_active_account and rng.random() < INTEGRATION_SHARE:
            integration_no += 1
            integrations.append(
                ClinicIntegration(
                    id=f"ci_{integration_no}",
                    clinic_id=clinic.id,
                    vendor=_weighted(rng, CLINIC_VENDOR_WEIGHTS),
                    auth_status=AuthStatus.VALID,
                    token_expires_at=anchor + timedelta(days=rng.randint(30, 365)),
                    endpoint_status=EndpointStatus.HEALTHY,
                    updated_at=anchor - timedelta(days=rng.randint(1, 30)),
                )
            )
        clinics.append(_Clinic(clinic, specialists, integrations))

    if not any(c.row.has_active_account for c in clinics):
        clinics[0] = replace(clinics[0], row=replace(clinics[0].row, has_active_account=True))
    for c in clinics:
        world.clinics.append(c.row)
        world.specialists.extend(c.specialists)
        world.clinic_integrations.extend(c.integrations)
    return clinics


# --- Referral lifecycle ---------------------------------------------------------------------


@dataclass
class _Action:
    """A planned recipient action, with its return-leg timing (None for DOWNLOADED)."""

    kind: RecipientActionType
    at: datetime
    callback_at: datetime | None = None


@dataclass
class ReferralPlan:
    """Every timing and choice for one referral, drawn before anything is emitted."""

    index: int
    rng: Random
    created_at: datetime
    practice: _Practice
    gp_user: GpUser
    clinic: _Clinic
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
    # Leg 3, deliver: (channel, target, attempted_at, latency_ms, http_status)
    deliveries: list[tuple[DeliveryChannel, str, datetime, int, int]] = field(init=False)
    actions: list[_Action] = field(init=False)

    def plan(self) -> None:
        self.compose()
        self.ingest()
        self.deliver()
        self.respond()
        self.return_leg()

    def compose(self) -> None:
        rng = self.rng
        self.submitted_at = self.created_at + _seconds(rng, 120, 1200)
        count = rng.choices([0, 1, 2, 3, 4], weights=[15, 30, 30, 15, 10])[0]
        titles = rng.sample(DOCUMENT_TITLES, count)
        self.documents = [
            (title, int(math.exp(rng.uniform(math.log(50_000), math.log(2_000_000)))))
            for title in titles
        ]
        self.generated_at = self.submitted_at + _seconds(rng, 5, 40)
        self.pdf_size_bytes = sum(size for _, size in self.documents) + rng.randint(80_000, 200_000)
        self.outbox_at = self.generated_at + _seconds(rng, 1, 5)

    def ingest(self) -> None:
        rng = self.rng
        self.sent_at = self.generated_at + _seconds(rng, 1, 3)
        self.hub_created_at = self.sent_at + _seconds(rng, 0.2, 1.5)
        self.file_fetched_at = self.hub_created_at + _seconds(rng, 2, 10)
        self.accepted_at = self.file_fetched_at + _seconds(rng, 0, 2)
        self.accept_notified_at = self.accepted_at + _seconds(rng, 0.1, 2)

    def deliver(self) -> None:
        rng = self.rng
        targets: list[tuple[DeliveryChannel, str, int]] = [
            (DeliveryChannel.EMAIL, "clinic-email", 202),
            (DeliveryChannel.PORTAL, "portal", 200),
        ]
        targets += [(DeliveryChannel.CLINIC_PMS, i.id, 201) for i in self.clinic.integrations]
        self.deliveries = [
            (
                channel,
                target,
                self.accepted_at + _seconds(rng, 1, 60),
                rng.randint(150, 3000),
                status,
            )
            for channel, target, status in targets
        ]

    def respond(self) -> None:
        rng = self.rng
        delivered = max(at for _, _, at, _, _ in self.deliveries)
        viewed = _after_business_days(rng, delivered, 3)
        actions = [_Action(RecipientActionType.VIEWED, viewed)]
        if rng.random() < 0.5:
            actions.append(
                _Action(RecipientActionType.DOWNLOADED, viewed + _seconds(rng, 60, 1800))
            )
        roll = rng.random()
        responded = _after_business_days(rng, viewed, 5)
        if roll < 0.85:
            actions.append(_Action(RecipientActionType.ACKNOWLEDGED, responded))
        elif roll < 0.95:
            actions.append(_Action(RecipientActionType.DECLINED, responded))
        else:
            actions.append(_Action(RecipientActionType.MESSAGE, responded))
            later = _after_business_days(rng, responded, 2)
            actions.append(_Action(RecipientActionType.ACKNOWLEDGED, later))
        self.actions = sorted(actions, key=lambda a: a.at)

    def return_leg(self) -> None:
        rng = self.rng
        for action in self.actions:
            if action.kind is not RecipientActionType.DOWNLOADED:
                action.callback_at = action.at + _seconds(rng, 1, 10)


def _plan_referrals(
    config: SimConfig, fake: Faker, practices: list[_Practice], clinics: list[_Clinic]
) -> list[ReferralPlan]:
    seed = config.seed
    eligible = [c for c in clinics if c.row.has_active_account]
    plans = []
    for index in range(1, config.referrals + 1):
        rng = rng_for(seed, "referral", index)
        created = _created_at(rng, config)
        practice = rng.choice(practices)
        clinic = rng.choice(eligible)
        named = rng.random() < NAMED_SPECIALIST_SHARE
        specialist = rng.choice(clinic.specialists) if named else None
        fake.seed_instance(derive_seed(seed, "patient", index))
        anchor_day = config.anchor_time.astimezone(NZ).date()
        plan = ReferralPlan(
            index=index,
            rng=rng,
            created_at=created,
            practice=practice,
            gp_user=rng.choice(practice.users),
            clinic=clinic,
            specialist=specialist,
            correlation_id=f"corr-{derive_seed(seed, 'correlation', index):016x}",
            patient_names=(fake.first_name(), fake.last_name()),
            date_of_birth=anchor_day - timedelta(days=rng.randint(365, 95 * 365)),
        )
        plan.plan()
        plans.append(plan)
    plans.sort(key=lambda p: (p.created_at, p.index))
    return plans


# --- Emitting rows --------------------------------------------------------------------------


def _emit_composer(
    plan: ReferralPlan,
    number: int,
    hub_id: str | None,
    action_ids: dict[int, str],
    config: SimConfig,
    world: World,
) -> None:
    def happened(moment: datetime) -> bool:
        return moment <= config.anchor_time

    referral_id = f"GR-{number}"
    events: list[tuple[datetime, str, str | None, str | None]] = []
    status = ReferralStatus.DRAFT
    submitted_at = sent_at = None
    pdf_size: int | None = None

    if happened(plan.submitted_at):
        status, submitted_at = ReferralStatus.SUBMITTED, plan.submitted_at
        events.append((plan.submitted_at, "SUBMITTED", None, "Submitted by the GP"))
    if happened(plan.generated_at):
        status, pdf_size = ReferralStatus.GENERATED, plan.pdf_size_bytes
        for k, (title, size) in enumerate(plan.documents, start=1):
            world.source_documents.append(
                SourceDocument(
                    f"doc_{number * 10 + k}", referral_id, title, size, FetchStatus.FETCHED, None
                )
            )
        events.append(
            (
                plan.generated_at,
                "SOURCE_DOCUMENTS_FETCHED",
                None,
                f"{len(plan.documents)} documents",
            )
        )
        events.append((plan.generated_at, "PDF_GENERATED", None, f"{plan.pdf_size_bytes} bytes"))
    if happened(plan.outbox_at):
        events.append((plan.outbox_at, "FILED_TO_OUTBOX", None, "Copy filed to the GP PMS outbox"))
    if hub_id is not None:
        status, sent_at = ReferralStatus.SENT, plan.sent_at
        world.hub_sends.append(
            HubSend(f"hs_{number * 10 + 1}", referral_id, 1, 201, None, plan.sent_at)
        )
        events.append((plan.sent_at, "SENT_TO_HUB", None, f"Hub referral {hub_id}"))
    if hub_id is not None and happened(plan.accept_notified_at):
        status = ReferralStatus.RECEIVED
        events.append((plan.accept_notified_at, "HUB_ACCEPTED", None, "Hub accepted the referral"))

    response_no = 0
    for k, action in enumerate(plan.actions, start=1):
        if action.callback_at is None or not happened(action.callback_at) or k not in action_ids:
            continue
        response_no += 1
        kind = ResponseType(action.kind.value)
        world.inbound_responses.append(
            InboundResponse(
                id=f"ir_{number * 100 + response_no}",
                referral_id=referral_id,
                hub_action_ref=action_ids[k],
                type=kind,
                received_at=action.callback_at,
                inbox_filing_status=InboxFilingStatus.FILED,
                filing_error=None,
            )
        )
        events.append((action.callback_at, "RESPONSE_RECEIVED", kind.value, None))
        events.append(
            (action.callback_at, "RESPONSE_FILED", kind.value, "Filed to the GP PMS inbox")
        )
        if kind is ResponseType.ACKNOWLEDGED:
            status = ReferralStatus.ACKNOWLEDGED
        elif kind is ResponseType.DECLINED:
            status = ReferralStatus.DECLINED

    events.sort(key=lambda e: e[0])  # stable: same-moment events keep their leg order
    for seq, (moment, event, sub_type, detail) in enumerate(events, start=1):
        world.referral_events.append(
            ReferralEvent(
                f"rev_{number * 100 + seq}",
                referral_id,
                event,
                sub_type,
                EventLevel.INFO,
                detail,
                moment,
            )
        )
    first, last = plan.patient_names
    world.patients.append(
        PatientSnapshot(
            id=f"pt_{number}",
            practice_id=plan.practice.row.id,
            synthetic_nhi=f"SIM{plan.index:04d}",
            given_name=first,
            family_name=last,
            date_of_birth=plan.date_of_birth.strftime("%d-%m-%Y"),
        )
    )
    world.referrals.append(
        Referral(
            id=referral_id,
            practice_id=plan.practice.row.id,
            gp_user_id=plan.gp_user.id,
            patient_id=f"pt_{number}",
            recipient_clinic_ref=plan.clinic.row.id,
            recipient_specialist_ref=plan.specialist.id if plan.specialist else None,
            correlation_id=plan.correlation_id,
            status=status,
            error_level=ErrorLevel.NONE,
            hub_referral_id=hub_id,
            pdf_size_bytes=pdf_size,
            created_at=plan.created_at,
            submitted_at=submitted_at,
            sent_at=sent_at,
        )
    )


def _emit_hub(plan: ReferralPlan, number: int, config: SimConfig, world: World) -> dict[int, str]:
    """Emit the Hub rows; return the ids of emitted recipient actions, by position in the plan."""

    def happened(moment: datetime) -> bool:
        return moment <= config.anchor_time

    hub_id = f"HR-{number}"
    accepted = happened(plan.accepted_at)
    fetched = happened(plan.file_fetched_at)
    world.hub_files.append(
        HubFile(
            id=f"hf_{number * 10 + 1}",
            hub_referral_id=hub_id,
            kind=HubFileKind.PDF,
            fetch_status=FetchStatus.FETCHED if fetched else FetchStatus.PENDING,
            size_bytes=plan.pdf_size_bytes if fetched else None,
            fetch_error=None,
            updated_at=plan.file_fetched_at if fetched else plan.hub_created_at,
        )
    )
    status = HubReferralStatus.NEW
    action_ids: dict[int, str] = {}
    if accepted:
        for k, (channel, target, attempted_at, latency, http_status) in enumerate(
            plan.deliveries, start=1
        ):
            delivery_no = number * 10 + k
            done = happened(attempted_at)
            world.deliveries.append(
                Delivery(
                    id=f"dl_{delivery_no}",
                    hub_referral_id=hub_id,
                    channel=channel,
                    target_ref=target,
                    status=DeliveryStatus.SUCCEEDED if done else DeliveryStatus.PENDING,
                    last_error_code=None,
                    updated_at=attempted_at if done else plan.accepted_at,
                )
            )
            if done:
                world.delivery_attempts.append(
                    DeliveryAttempt(
                        f"da_{delivery_no * 10 + 1}",
                        f"dl_{delivery_no}",
                        1,
                        http_status,
                        None,
                        None,
                        latency,
                        attempted_at,
                    )
                )
        actor = plan.specialist or plan.rng.choice(plan.clinic.specialists)
        for k, action in enumerate(plan.actions, start=1):
            if not happened(action.at):
                continue
            action_id = f"ra_{number * 10 + k}"
            action_ids[k] = action_id
            world.recipient_actions.append(
                RecipientAction(action_id, hub_id, action.kind, actor.id, action.at)
            )
            if action.kind is RecipientActionType.VIEWED and status is HubReferralStatus.NEW:
                status = HubReferralStatus.VIEWED
            elif action.kind is RecipientActionType.ACKNOWLEDGED:
                status = HubReferralStatus.ACKNOWLEDGED
            elif action.kind is RecipientActionType.DECLINED:
                status = HubReferralStatus.DECLINED
            if action.callback_at is not None and happened(action.callback_at):
                world.return_callbacks.append(
                    ReturnCallback(
                        f"rc_{(number * 10 + k) * 10 + 1}",
                        hub_id,
                        action_id,
                        1,
                        200,
                        CallbackStatus.SUCCEEDED,
                        action.callback_at,
                    )
                )
    world.hub_referrals.append(
        HubReferral(
            id=hub_id,
            source_ref=f"GR-{plan.number}",
            correlation_id=plan.correlation_id,
            clinic_id=plan.clinic.row.id,
            specialist_id=plan.specialist.id if plan.specialist else None,
            routing_mode=RoutingMode.ELECTRONIC if accepted else None,
            status=status,
            created_at=plan.hub_created_at,
            accepted_at=plan.accepted_at if accepted else None,
        )
    )
    return action_ids


def generate(config: SimConfig) -> World:
    """Build the complete healthy world for `config`. Pure: no I/O, same config, same world."""
    world = World()
    fake = Faker("en_NZ")
    practices = _practices(config, fake, world)
    clinics = _clinics(config, fake, world)
    plans = _plan_referrals(config, fake, practices, clinics)

    for number, plan in enumerate(plans, start=10001):
        plan.number = number
    # The Hub numbers referrals in the order it receives them.
    at_hub = sorted(
        (p for p in plans if p.hub_created_at <= config.anchor_time),
        key=lambda p: (p.hub_created_at, p.number),
    )
    # It also receives referrals from other channels, so its numbering has gaps.
    gaps = rng_for(config.seed, "hub-numbering")
    hub_ids: dict[int, str] = {}
    action_ids: dict[int, dict[int, str]] = {}
    hub_number = 50000
    for plan in at_hub:
        hub_number += 1 + gaps.choice([0, 0, 0, 1, 2, 4])
        hub_ids[plan.number] = f"HR-{hub_number}"
        action_ids[plan.number] = _emit_hub(plan, hub_number, config, world)
    for plan in plans:
        _emit_composer(
            plan,
            plan.number,
            hub_ids.get(plan.number),
            action_ids.get(plan.number, {}),
            config,
            world,
        )
    return world
