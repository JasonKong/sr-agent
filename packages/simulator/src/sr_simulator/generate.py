"""Generate a world: participants, and referrals that travel all four legs.

Each referral is built in two steps:

1. **Plan** (`plan.py`). Every random value for every leg is drawn up front from the
   referral's own stream; `faults.py` may then replace one leg's outcome.
2. **Emit** (this module). The plan becomes rows, leaving out anything scheduled after the
   anchor time. That part has not happened yet, which is why recent referrals are still in
   progress.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
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
    FailureCode,
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
from sr_simulator.faults import assign_faults
from sr_simulator.plan import ClinicInfo, PlannedDelivery, Practice, ReferralPlan
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
from sr_simulator.scenario import injections, load_scenarios
from sr_simulator.timeline import created_at

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


def _weighted[T](rng: Random, weights: dict[T, int]) -> T:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _person(fake: Faker, title: str = "") -> str:
    return f"{title}{fake.first_name()} {fake.last_name()}"


# --- Participants ---------------------------------------------------------------------------


def _practices(config: SimConfig, fake: Faker, world: World) -> list[Practice]:
    seed, anchor = config.seed, config.anchor_time
    names = list(product(PRACTICE_WORDS, PRACTICE_SUFFIXES))
    rng_for(seed, "practice-names").shuffle(names)
    practices: list[Practice] = []
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
        practices.append(Practice(practice, users))
    return practices


def _clinics(config: SimConfig, fake: Faker, world: World) -> list[ClinicInfo]:
    seed, anchor = config.seed, config.anchor_time
    names = list(product(CLINIC_WORDS, SPECIALTIES))
    rng_for(seed, "clinic-names").shuffle(names)
    clinics: list[ClinicInfo] = []
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
        clinics.append(ClinicInfo(clinic, specialists, integrations))

    # Healthy referrals need a clinic with an account, and F06 needs one without.
    if not any(c.row.has_active_account for c in clinics):
        clinics[0] = replace(clinics[0], row=replace(clinics[0].row, has_active_account=True))
    if len(clinics) > 1 and all(c.row.has_active_account for c in clinics):
        last = clinics[-1]
        clinics[-1] = replace(
            last, row=replace(last.row, has_active_account=False), integrations=[]
        )
    for c in clinics:
        world.clinics.append(c.row)
        world.specialists.extend(c.specialists)
        world.clinic_integrations.extend(c.integrations)
    return clinics


def _plan_referrals(
    config: SimConfig, fake: Faker, practices: list[Practice], clinics: list[ClinicInfo]
) -> list[ReferralPlan]:
    seed = config.seed
    eligible = [c for c in clinics if c.row.has_active_account]
    anchor_day = config.anchor_time.astimezone(NZ).date()
    plans = []
    for index in range(1, config.referrals + 1):
        rng = rng_for(seed, "referral", index)
        created = created_at(rng, config)
        practice = rng.choice(practices)
        clinic = rng.choice(eligible)
        named = rng.random() < NAMED_SPECIALIST_SHARE
        specialist = rng.choice(clinic.specialists) if named else None
        fake.seed_instance(derive_seed(seed, "patient", index))
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
    for number, plan in enumerate(plans, start=10001):
        plan.number = number
    return plans


# --- Emitting rows --------------------------------------------------------------------------

_Event = tuple[datetime, str, str | None, EventLevel, str | None]
INFO, WARN, ERROR = EventLevel.INFO, EventLevel.WARN, EventLevel.ERROR


class _Emitter:
    def __init__(self, config: SimConfig, world: World) -> None:
        self.anchor = config.anchor_time
        self.world = world

    def happened(self, moment: datetime | None) -> bool:
        return moment is not None and moment <= self.anchor

    # --- Hub ---------------------------------------------------------------------------------

    def hub(self, plan: ReferralPlan, number: int) -> dict[int, str]:
        """Emit the Hub rows; return emitted recipient action ids by 1-based plan position."""
        world, hub_id = self.world, f"HR-{number}"
        accepted = self.happened(plan.accepted_at) and plan.fetch_failure is None
        world.hub_files.append(self._hub_file(plan, number, hub_id))

        status = HubReferralStatus.NEW
        action_ids: dict[int, str] = {}
        if accepted:
            for k, delivery in enumerate(plan.deliveries, start=1):
                self._delivery(plan, hub_id, number * 10 + k, delivery)
            actor = plan.specialist or plan.rng.choice(plan.clinic.specialists)
            for k, action in enumerate(plan.actions, start=1):
                if not self.happened(action.at):
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
                if self.happened(action.callback_at):
                    assert action.callback_at is not None
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

        routing = None
        if accepted:
            routing = RoutingMode.MANUAL if plan.manual_routing else RoutingMode.ELECTRONIC
        world.hub_referrals.append(
            HubReferral(
                id=hub_id,
                source_ref=f"GR-{plan.number}",
                correlation_id=plan.correlation_id,
                clinic_id=plan.clinic.row.id,
                specialist_id=plan.specialist.id if plan.specialist else None,
                routing_mode=routing,
                status=status,
                created_at=plan.hub_created_at,
                accepted_at=plan.accepted_at if accepted else None,
            )
        )
        return action_ids

    def _hub_file(self, plan: ReferralPlan, number: int, hub_id: str) -> HubFile:
        file_id, pdf = f"hf_{number * 10 + 1}", HubFileKind.PDF
        if not self.happened(plan.file_fetched_at):
            return HubFile(
                file_id, hub_id, pdf, FetchStatus.PENDING, None, None, plan.hub_created_at
            )
        if plan.fetch_failure is not None:
            error, _, refetch_at = plan.fetch_failure
            last_try = refetch_at if self.happened(refetch_at) else plan.file_fetched_at
            return HubFile(file_id, hub_id, pdf, FetchStatus.FAILED, None, error, last_try)
        return HubFile(
            file_id,
            hub_id,
            pdf,
            FetchStatus.FETCHED,
            plan.pdf_size_bytes,
            None,
            plan.file_fetched_at,
        )

    def _delivery(
        self, plan: ReferralPlan, hub_id: str, delivery_no: int, delivery: PlannedDelivery
    ) -> None:
        world, delivery_id = self.world, f"dl_{delivery_no}"
        failure = plan.pms_failure if delivery.channel is DeliveryChannel.CLINIC_PMS else None

        if failure is not None and self.happened(delivery.attempted_at):
            done = [a for a in failure.attempts if self.happened(a[0])]
            failed = self.happened(failure.failed_at)
            for n, (at, http_status, message, latency) in enumerate(done, start=1):
                world.delivery_attempts.append(
                    DeliveryAttempt(
                        f"da_{delivery_no * 10 + n}",
                        delivery_id,
                        n,
                        http_status,
                        failure.error_code.value,
                        message,
                        latency,
                        at,
                    )
                )
            updated = failure.failed_at if failed else (done[-1][0] if done else plan.accepted_at)
            world.deliveries.append(
                Delivery(
                    id=delivery_id,
                    hub_referral_id=hub_id,
                    channel=delivery.channel,
                    target_ref=delivery.target,
                    status=DeliveryStatus.FAILED if failed else DeliveryStatus.PENDING,
                    last_error_code=failure.error_code.value if failed or done else None,
                    updated_at=updated,
                )
            )
            return

        done_ok = self.happened(delivery.attempted_at)
        world.deliveries.append(
            Delivery(
                id=delivery_id,
                hub_referral_id=hub_id,
                channel=delivery.channel,
                target_ref=delivery.target,
                status=DeliveryStatus.SUCCEEDED if done_ok else DeliveryStatus.PENDING,
                last_error_code=None,
                updated_at=delivery.attempted_at if done_ok else plan.accepted_at,
            )
        )
        if done_ok:
            world.delivery_attempts.append(
                DeliveryAttempt(
                    f"da_{delivery_no * 10 + 1}",
                    delivery_id,
                    1,
                    delivery.http_status,
                    None,
                    None,
                    delivery.latency_ms,
                    delivery.attempted_at,
                )
            )

    # --- Composer ----------------------------------------------------------------------------

    def composer(self, plan: ReferralPlan, hub_id: str | None, action_ids: dict[int, str]) -> None:
        world, number = self.world, plan.number
        referral_id = f"GR-{number}"
        events: list[_Event] = []
        status, error_level = ReferralStatus.DRAFT, ErrorLevel.NONE
        submitted_at = sent_at = None
        pdf_size: int | None = None

        # Leg 1: compose. Validation can stop it before generation (F01).
        compose_ok = plan.validation_error is None
        if plan.validation_error is not None and self.happened(plan.submitted_at):
            field_name, detail = plan.validation_error
            status, error_level = ReferralStatus.ERROR, ErrorLevel.CRITICAL
            events.append((plan.submitted_at, "VALIDATION_FAILED", field_name, ERROR, detail))
        elif compose_ok and self.happened(plan.submitted_at):
            status, submitted_at = ReferralStatus.SUBMITTED, plan.submitted_at
            events.append((plan.submitted_at, "SUBMITTED", None, INFO, "Submitted by the GP"))

        if compose_ok and self.happened(plan.generated_at):
            fetched = 0
            for k, (title, size) in enumerate(plan.documents, start=1):
                skipped = plan.skipped_document is not None and plan.skipped_document[0] == k - 1
                fetch_error = (
                    plan.skipped_document[1] if plan.skipped_document and skipped else None
                )
                world.source_documents.append(
                    SourceDocument(
                        f"doc_{number * 10 + k}",
                        referral_id,
                        title,
                        size,
                        FetchStatus.FAILED if skipped else FetchStatus.FETCHED,
                        fetch_error,
                    )
                )
                fetched += 0 if skipped else 1
            detail = f"{fetched} documents"
            events.append((plan.generated_at, "SOURCE_DOCUMENTS_FETCHED", None, INFO, detail))
            if plan.pdf_failure is not None:  # F02
                compose_ok = False
                original, compressed, retry_at, failed_at = plan.pdf_failure
                detail = f"PDF is {original} bytes"
                events.append((plan.generated_at, "PDF_TOO_LARGE", None, WARN, detail))
                if self.happened(retry_at):
                    events.append((retry_at, "PDF_COMPRESSION_RETRY", None, INFO, "Compressing"))
                if self.happened(failed_at):
                    status, error_level = ReferralStatus.ERROR, ErrorLevel.CRITICAL
                    detail = f"Still {compressed} bytes after compression; over the size limit"
                    events.append((failed_at, "PDF_GENERATION_FAILED", None, ERROR, detail))
            else:
                status, pdf_size = ReferralStatus.GENERATED, plan.pdf_size_bytes
                detail = f"{plan.pdf_size_bytes} bytes"
                events.append((plan.generated_at, "PDF_GENERATED", None, INFO, detail))
        if compose_ok and self.happened(plan.outbox_at):
            detail = "Copy filed to the GP PMS outbox"
            events.append((plan.outbox_at, "FILED_TO_OUTBOX", None, INFO, detail))

        # Leg 2: hand-off. Composer resends once if the Hub fails or has not accepted it.
        if compose_ok and plan.hub_send_failures:  # F04
            for attempt, (at, http_status) in enumerate(plan.hub_send_failures, start=1):
                if not self.happened(at):
                    continue
                if attempt > 1 and plan.auto_resend_at is not None:
                    detail = "Hub has not confirmed the referral; resending"
                    events.append((plan.auto_resend_at, "AUTO_RESEND", None, INFO, detail))
                message = f"Hub returned HTTP {http_status}"
                world.hub_sends.append(
                    HubSend(
                        f"hs_{number * 10 + attempt}",
                        referral_id,
                        attempt,
                        http_status,
                        message,
                        at,
                    )
                )
                events.append((at, "HUB_SEND_FAILED", None, ERROR, f"HTTP {http_status}"))
                error_level = ErrorLevel.NON_CRITICAL
        if hub_id is not None:
            status, sent_at = ReferralStatus.SENT, plan.sent_at
            world.hub_sends.append(
                HubSend(f"hs_{number * 10 + 1}", referral_id, 1, 201, None, plan.sent_at)
            )
            events.append((plan.sent_at, "SENT_TO_HUB", None, INFO, f"Hub referral {hub_id}"))
            if plan.fetch_failure is not None:  # F05
                resend_at = plan.fetch_failure[1]
                if self.happened(resend_at):
                    error_level = ErrorLevel.NON_CRITICAL
                    detail = "Hub has not accepted the referral"
                    events.append((resend_at, "CHECK_RECEIVED", None, WARN, detail))
                    detail = "Hub has not confirmed the referral; resending"
                    events.append((resend_at, "AUTO_RESEND", None, INFO, detail))
                    world.hub_sends.append(
                        HubSend(f"hs_{number * 10 + 2}", referral_id, 2, 201, None, resend_at)
                    )
            elif self.happened(plan.accept_notified_at):
                status = ReferralStatus.RECEIVED
                detail = "Hub accepted the referral"
                events.append((plan.accept_notified_at, "HUB_ACCEPTED", None, INFO, detail))

        # Leg 4: responses returned to Composer and filed in the GP PMS inbox.
        response_no = 0
        for k, action in enumerate(plan.actions, start=1):
            if k not in action_ids or not self.happened(action.callback_at):
                continue
            assert action.callback_at is not None
            response_no += 1
            kind = ResponseType(action.kind.value)
            failing = plan.filing_failure is not None and plan.filing_failure[0] == k - 1
            filing_error = plan.filing_failure[1] if plan.filing_failure and failing else None
            world.inbound_responses.append(
                InboundResponse(
                    id=f"ir_{number * 100 + response_no}",
                    referral_id=referral_id,
                    hub_action_ref=action_ids[k],
                    type=kind,
                    received_at=action.callback_at,
                    inbox_filing_status=(
                        InboxFilingStatus.FAILED if failing else InboxFilingStatus.FILED
                    ),
                    filing_error=filing_error,
                )
            )
            events.append((action.callback_at, "RESPONSE_RECEIVED", kind.value, INFO, None))
            if failing:  # F11
                error_level = ErrorLevel.NON_CRITICAL
                events.append(
                    (action.callback_at, "RESPONSE_FILING_FAILED", kind.value, ERROR, filing_error)
                )
            else:
                detail = "Filed to the GP PMS inbox"
                events.append((action.callback_at, "RESPONSE_FILED", kind.value, INFO, detail))
            if kind is ResponseType.ACKNOWLEDGED:
                status = ReferralStatus.ACKNOWLEDGED
            elif kind is ResponseType.DECLINED:
                status = ReferralStatus.DECLINED

        events.sort(key=lambda e: e[0])  # stable: same-moment events keep their leg order
        for seq, (moment, event, sub_type, level, detail_text) in enumerate(events, start=1):
            world.referral_events.append(
                ReferralEvent(
                    f"rev_{number * 100 + seq}",
                    referral_id,
                    event,
                    sub_type,
                    level,
                    detail_text,
                    moment,
                )
            )
        first, last = plan.patient_names
        world.patients.append(
            PatientSnapshot(
                id=f"pt_{number}",
                practice_id=plan.practice.row.id,
                synthetic_nhi=plan.synthetic_nhi,
                given_name=first,
                family_name=last,
                date_of_birth=plan.date_of_birth_text,
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
                error_level=error_level,
                hub_referral_id=hub_id,
                pdf_size_bytes=pdf_size,
                created_at=plan.created_at,
                submitted_at=submitted_at,
                sent_at=sent_at,
            )
        )


def generate(config: SimConfig) -> World:
    """Build the complete world for `config`. Pure: no I/O; same config, same world."""
    world = World()
    fake = Faker("en_NZ")
    practices = _practices(config, fake, world)
    clinics = _clinics(config, fake, world)
    plans = _plan_referrals(config, fake, practices, clinics)

    if config.failure_rate > 0:
        overrides = assign_faults(config, plans, clinics, injections(load_scenarios()))
        world.clinic_integrations = [overrides.get(i.id, i) for i in world.clinic_integrations]

    # The Hub numbers referrals in the order it receives them. It also receives referrals
    # from other channels, so its numbering has gaps.
    emit = _Emitter(config, world)
    at_hub = sorted(
        (
            p
            for p in plans
            if not p.hub_send_failures
            and p.validation_error is None
            and p.pdf_failure is None
            and emit.happened(p.hub_created_at)
        ),
        key=lambda p: (p.hub_created_at, p.number),
    )
    gaps = rng_for(config.seed, "hub-numbering")
    hub_ids: dict[int, str] = {}
    action_ids: dict[int, dict[int, str]] = {}
    hub_number = 50000
    for plan in at_hub:
        hub_number += 1 + gaps.choice([0, 0, 0, 1, 2, 4])
        hub_ids[plan.number] = f"HR-{hub_number}"
        action_ids[plan.number] = emit.hub(plan, hub_number)
    for plan in plans:
        emit.composer(plan, hub_ids.get(plan.number), action_ids.get(plan.number, {}))

    world.labels = {
        f"GR-{p.number}": p.label for p in plans if p.label is not FailureCode.F00_NO_FAULT
    }
    return world
