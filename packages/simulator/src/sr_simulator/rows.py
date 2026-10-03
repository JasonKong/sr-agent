"""One immutable dataclass per table (DESIGN.md section 3.3), and the World that holds them.

Field names are the column names; `write.py` copies rows into the table of the same name.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

# --- Composer -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GpPractice:
    id: str
    name: str
    pms_vendor: GpPmsVendor
    status: ActiveStatus
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GpUser:
    id: str
    practice_id: str
    name: str
    role: GpUserRole


@dataclass(frozen=True, slots=True)
class PmsConnection:
    id: str
    practice_id: str
    auth_status: AuthStatus
    token_expires_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PatientSnapshot:
    id: str
    practice_id: str
    synthetic_nhi: str | None
    given_name: str
    family_name: str
    date_of_birth: str | None


@dataclass(frozen=True, slots=True)
class Referral:
    id: str
    practice_id: str
    gp_user_id: str
    patient_id: str
    recipient_clinic_ref: str
    recipient_specialist_ref: str | None
    correlation_id: str
    status: ReferralStatus
    error_level: ErrorLevel
    hub_referral_id: str | None
    pdf_size_bytes: int | None
    created_at: datetime
    submitted_at: datetime | None
    sent_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReferralEvent:
    id: str
    referral_id: str
    event: str
    sub_type: str | None
    level: EventLevel
    detail: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SourceDocument:
    id: str
    referral_id: str
    title: str
    size_bytes: int
    fetch_status: FetchStatus
    fetch_error: str | None


@dataclass(frozen=True, slots=True)
class HubSend:
    id: str
    referral_id: str
    attempt_no: int
    http_status: int | None
    error_message: str | None
    attempted_at: datetime


@dataclass(frozen=True, slots=True)
class InboundResponse:
    id: str
    referral_id: str
    hub_action_ref: str | None
    type: ResponseType
    received_at: datetime
    inbox_filing_status: InboxFilingStatus
    filing_error: str | None


# --- Hub ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Clinic:
    id: str
    name: str
    specialty_group: str
    status: ActiveStatus
    has_active_account: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Specialist:
    id: str
    clinic_id: str
    name: str
    status: ActiveStatus


@dataclass(frozen=True, slots=True)
class ClinicIntegration:
    id: str
    clinic_id: str
    vendor: ClinicPmsVendor
    auth_status: AuthStatus
    token_expires_at: datetime | None
    endpoint_status: EndpointStatus
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class HubReferral:
    id: str
    source_ref: str
    correlation_id: str
    clinic_id: str
    specialist_id: str | None
    routing_mode: RoutingMode | None
    status: HubReferralStatus
    created_at: datetime
    accepted_at: datetime | None


@dataclass(frozen=True, slots=True)
class HubFile:
    id: str
    hub_referral_id: str
    kind: HubFileKind
    fetch_status: FetchStatus
    size_bytes: int | None
    fetch_error: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Delivery:
    id: str
    hub_referral_id: str
    channel: DeliveryChannel
    target_ref: str
    status: DeliveryStatus
    last_error_code: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    id: str
    delivery_id: str
    attempt_no: int
    http_status: int | None
    error_code: str | None
    error_message: str | None
    latency_ms: int | None
    attempted_at: datetime


@dataclass(frozen=True, slots=True)
class RecipientAction:
    id: str
    hub_referral_id: str
    action: RecipientActionType
    actor_ref: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ReturnCallback:
    id: str
    hub_referral_id: str
    action_id: str
    attempt_no: int
    http_status: int | None
    status: CallbackStatus
    attempted_at: datetime


# --- Knowledge --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupportTicket:
    id: str
    referral_ref: str | None
    clinic_ref: str | None
    title: str
    description: str
    resolution: str | None
    root_cause_code: FailureCode | None
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class Runbook:
    id: str
    slug: str
    error_code: FailureCode | None
    title: str
    body_markdown: str
    updated_at: datetime


# --- Logs -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LogLine:
    """One structured log line (DESIGN.md section 3.3). Not a database table: logs are JSONL.

    `referral_id` is the id native to the logging system (GR- in Composer, HR- in the Hub);
    `correlation_id` is shared across both. `id` is `<service>:<sequence>`.
    """

    id: str
    timestamp: datetime
    level: EventLevel
    service: str
    referral_id: str
    correlation_id: str
    leg: str
    event: str
    error_code: str | None
    message: str


# --- World ----------------------------------------------------------------------------------


@dataclass
class World:
    """Every generated row, grouped by table. Lists are in a deterministic order."""

    gp_practices: list[GpPractice] = field(default_factory=list)
    gp_users: list[GpUser] = field(default_factory=list)
    pms_connections: list[PmsConnection] = field(default_factory=list)
    patients: list[PatientSnapshot] = field(default_factory=list)
    referrals: list[Referral] = field(default_factory=list)
    referral_events: list[ReferralEvent] = field(default_factory=list)
    source_documents: list[SourceDocument] = field(default_factory=list)
    hub_sends: list[HubSend] = field(default_factory=list)
    inbound_responses: list[InboundResponse] = field(default_factory=list)
    clinics: list[Clinic] = field(default_factory=list)
    specialists: list[Specialist] = field(default_factory=list)
    clinic_integrations: list[ClinicIntegration] = field(default_factory=list)
    hub_referrals: list[HubReferral] = field(default_factory=list)
    hub_files: list[HubFile] = field(default_factory=list)
    deliveries: list[Delivery] = field(default_factory=list)
    delivery_attempts: list[DeliveryAttempt] = field(default_factory=list)
    recipient_actions: list[RecipientAction] = field(default_factory=list)
    return_callbacks: list[ReturnCallback] = field(default_factory=list)
    support_tickets: list[SupportTicket] = field(default_factory=list)
    runbooks: list[Runbook] = field(default_factory=list)
    logs: list[LogLine] = field(default_factory=list)
    # Ground truth, never written to the database: Composer id -> injected failure code.
    # Referrals without an entry are F00_NO_FAULT.
    labels: dict[str, FailureCode] = field(default_factory=dict)
    # Composer id -> distractor kind. The true code is still `labels` (F00 when absent).
    distractors: dict[str, str] = field(default_factory=dict)
    # Ticket id -> knowledge distractor kind (misleading_ticket, injection_in_ticket).
    knowledge_labels: dict[str, str] = field(default_factory=dict)

    def tables(self) -> list[tuple[str, Sequence[DataclassInstance]]]:
        """(qualified table name, rows) pairs, parents before children."""
        return [
            ("composer.gp_practice", self.gp_practices),
            ("composer.gp_user", self.gp_users),
            ("composer.pms_connection", self.pms_connections),
            ("composer.patient_snapshot", self.patients),
            ("composer.referral", self.referrals),
            ("composer.referral_event", self.referral_events),
            ("composer.source_document", self.source_documents),
            ("composer.hub_send", self.hub_sends),
            ("composer.inbound_response", self.inbound_responses),
            ("hub.clinic", self.clinics),
            ("hub.specialist", self.specialists),
            ("hub.clinic_integration", self.clinic_integrations),
            ("hub.hub_referral", self.hub_referrals),
            ("hub.hub_file", self.hub_files),
            ("hub.delivery", self.deliveries),
            ("hub.delivery_attempt", self.delivery_attempts),
            ("hub.recipient_action", self.recipient_actions),
            ("hub.return_callback", self.return_callbacks),
            ("knowledge.support_ticket", self.support_tickets),
            ("knowledge.runbook", self.runbooks),
        ]


ROW_TYPES: dict[str, type[DataclassInstance]] = {
    "composer.gp_practice": GpPractice,
    "composer.gp_user": GpUser,
    "composer.pms_connection": PmsConnection,
    "composer.patient_snapshot": PatientSnapshot,
    "composer.referral": Referral,
    "composer.referral_event": ReferralEvent,
    "composer.source_document": SourceDocument,
    "composer.hub_send": HubSend,
    "composer.inbound_response": InboundResponse,
    "hub.clinic": Clinic,
    "hub.specialist": Specialist,
    "hub.clinic_integration": ClinicIntegration,
    "hub.hub_referral": HubReferral,
    "hub.hub_file": HubFile,
    "hub.delivery": Delivery,
    "hub.delivery_attempt": DeliveryAttempt,
    "hub.recipient_action": RecipientAction,
    "hub.return_callback": ReturnCallback,
    "knowledge.support_ticket": SupportTicket,
    "knowledge.runbook": Runbook,
    "log": LogLine,  # evidence can point at log lines too
}
