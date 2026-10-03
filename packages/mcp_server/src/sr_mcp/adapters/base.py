"""Adapter interfaces and the records they return (DESIGN.md section 7.2).

Tools only see these interfaces, so a real organisation could swap in its own data
sources (section 11), and unit tests use fakes (`sr_mcp.testing`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from sr_mcp.masking import FieldStatus


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True)


class PatientFields(_Record):
    """A patient as tools may see one: a reference, and whether required fields are usable."""

    patient_ref: str
    synthetic_nhi: FieldStatus
    date_of_birth: FieldStatus


class PracticeInfo(_Record):
    id: str
    name: str
    pms_vendor: str


class PersonInfo(_Record):
    """A GP user or a specialist. These are clinicians, not patients, so names are allowed."""

    id: str
    name: str
    role: str | None = None


class ClinicInfo(_Record):
    id: str
    name: str
    specialty_group: str
    status: str
    has_active_account: bool


class SourceDocumentInfo(_Record):
    """A document Composer pulled from the GP's PMS into the referral PDF."""

    id: str
    title: str
    size_bytes: int
    fetch_status: str
    fetch_error: str | None


class HandOffAttempt(_Record):
    """One attempt by Composer to send the referral to the Hub."""

    id: str
    attempt_no: int
    http_status: int | None
    error_message: str | None
    attempted_at: datetime


class ComposerSide(_Record):
    id: str
    status: str
    error_level: str
    created_at: datetime
    submitted_at: datetime | None
    sent_at: datetime | None
    pdf_size_bytes: int | None
    practice: PracticeInfo
    gp_user: PersonInfo
    patient: PatientFields
    source_documents: list[SourceDocumentInfo] = []
    hand_offs: list[HandOffAttempt] = []


class HubSide(_Record):
    id: str
    status: str
    routing_mode: str | None
    created_at: datetime
    accepted_at: datetime | None


class Recipient(_Record):
    clinic: ClinicInfo | None
    specialist: PersonInfo | None


Link = Literal["linked", "composer_only", "hub_only"]


class ReferralView(_Record):
    """One referral seen from both systems. `link` says whether both sides exist."""

    link: Link
    correlation_id: str
    composer: ComposerSide | None
    hub: HubSide | None
    recipient: Recipient


class ReferralFilters(_Record):
    clinic: str | None = None  # clinic id, or part of its name
    practice: str | None = None  # GP practice id, or part of its name
    status: str | None = None  # Composer status
    with_errors: bool | None = None
    created_from: date | None = None
    created_to: date | None = None  # inclusive
    limit: int = 20


class ReferralSummary(_Record):
    composer_id: str
    hub_id: str | None
    created_at: datetime
    composer_status: str
    hub_status: str | None
    error_level: str
    has_error: bool  # an error recorded in either system
    clinic_name: str | None
    practice_name: str
    correlation_id: str


class TimelineEntry(_Record):
    at: datetime
    system: Literal["composer", "hub"]
    leg: str
    event: str
    level: str
    detail: str | None
    record_id: str  # cite this as evidence


class DeliveryAttemptInfo(_Record):
    id: str
    attempt_no: int
    http_status: int | None  # None: no response (for example a timeout)
    error_code: str | None
    error_message: str | None
    latency_ms: int | None
    attempted_at: datetime


class DeliveryInfo(_Record):
    id: str
    channel: str
    target_ref: str
    status: str
    last_error_code: str | None
    updated_at: datetime
    attempts: list[DeliveryAttemptInfo]  # may be empty when it failed before sending


class DeliveriesView(_Record):
    composer_id: str | None
    hub_referral_id: str | None
    routing_mode: str | None
    accepted_at: datetime | None
    note: str | None  # why there are no deliveries, when there are none
    deliveries: list[DeliveryInfo]


class IntegrationInfo(_Record):
    id: str
    vendor: str
    auth_status: str
    token_expires_at: datetime | None
    endpoint_status: str
    updated_at: datetime


class RecipientView(_Record):
    clinic: ClinicInfo
    routing_mode: Literal["ELECTRONIC", "MANUAL"]  # what the Hub does for this clinic
    specialists: list[PersonInfo]
    integrations: list[IntegrationInfo]


class CallbackInfo(_Record):
    id: str
    attempt_no: int
    http_status: int | None
    status: str
    attempted_at: datetime


class InboxFiling(_Record):
    id: str
    type: str
    received_at: datetime
    inbox_filing_status: str
    filing_error: str | None


class ReturnStep(_Record):
    """One recipient action, followed back to the GP: Hub callback, then GP inbox filing."""

    action_id: str
    action: str
    actor_ref: str | None
    occurred_at: datetime
    callbacks: list[CallbackInfo]  # empty for actions that are not returned (DOWNLOADED)
    gp_inbox: InboxFiling | None


class ReturnPathView(_Record):
    composer_id: str | None
    hub_referral_id: str | None
    note: str | None
    steps: list[ReturnStep]


class LogRecord(_Record):
    id: str
    timestamp: datetime
    level: str
    service: str
    referral_id: str
    correlation_id: str
    leg: str
    event: str
    error_code: str | None
    message: str


LEVELS = ("INFO", "WARN", "ERROR")


class LogQuery(_Record):
    referral_id: str | None = None  # the id native to the logging system
    correlation_id: str | None = None
    service: str | None = None
    min_level: Literal["INFO", "WARN", "ERROR"] | None = None  # this level and above
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 50


class FailedReferral(_Record):
    """A recorded failure, on one leg, with a short reason."""

    composer_id: str | None
    hub_id: str | None
    leg: Literal["compose", "ingest", "deliver", "return"]
    reason: str
    failed_at: datetime
    clinic_name: str | None


class RunbookSummary(_Record):
    slug: str
    error_code: str | None  # None for general guides
    title: str


class RunbookPage(RunbookSummary):
    body_markdown: str
    updated_at: datetime


class KnowledgeHit(_Record):
    source_type: Literal["ticket", "runbook"]
    source_id: str
    title: str
    text: str
    error_code: str | None
    score: float


class ReferralStore(Protocol):
    """Referral data from both systems."""

    def patient_fields(self, patient_id: str) -> PatientFields | None: ...

    def get_referral(self, referral_id: str) -> ReferralView | None:
        """By Composer (GR-) or Hub (HR-) id; None when neither system knows it."""
        ...

    def find_referrals(self, filters: ReferralFilters) -> list[ReferralSummary]:
        """Newest first, at most `filters.limit`."""
        ...

    def timeline(self, referral_id: str) -> list[TimelineEntry] | None:
        """Events from both systems in time order; None when the id is unknown."""
        ...

    def deliveries(self, referral_id: str) -> DeliveriesView | None:
        """Every delivery channel and attempt; None when the id is unknown."""
        ...

    def recipient(self, clinic_id: str) -> RecipientView | None: ...

    def return_path(self, referral_id: str) -> ReturnPathView | None:
        """Recipient actions followed back to the GP's inbox; None when the id is unknown."""
        ...

    def failed_referrals(
        self, since: datetime, leg: str | None, limit: int
    ) -> list[FailedReferral]:
        """Failures recorded since `since`, newest first."""
        ...

    def data_now(self) -> datetime:
        """The latest time in the data: "now" for questions such as "in the last day"."""
        ...

    def close(self) -> None: ...


class LogSource(Protocol):
    """Structured logs from both systems: JSONL files locally, CloudWatch in AWS (T2.4)."""

    def search(self, query: LogQuery) -> list[LogRecord]: ...


class KnowledgeSearch(Protocol):
    """Runbooks, and search over past tickets and runbooks (search arrives in T4.2)."""

    def search(self, query: str, *, error_code: str | None, top_k: int) -> list[KnowledgeHit]: ...

    def list_runbooks(self) -> list[RunbookSummary]: ...

    def runbook(self, key: str) -> RunbookPage | None:
        """By failure code (F07_...) or slug (guide-four-legs)."""
        ...
