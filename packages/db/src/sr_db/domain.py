"""Shared vocabulary of the simulated referral domain (DESIGN.md section 3).

Every value list here matches a CHECK constraint in the SQL migrations; the schema
integration tests keep the two in step. Each member's value equals its name.
"""

import re
from enum import StrEnum


class FailureCode(StrEnum):
    """Ground-truth root causes (DESIGN.md section 3.4)."""

    F00_NO_FAULT = "F00_NO_FAULT"
    F01_MISSING_REQUIRED_FIELD = "F01_MISSING_REQUIRED_FIELD"
    F02_PDF_TOO_LARGE = "F02_PDF_TOO_LARGE"
    F03_SOURCE_DOC_SKIPPED = "F03_SOURCE_DOC_SKIPPED"
    F04_HUB_UNAVAILABLE = "F04_HUB_UNAVAILABLE"
    F05_ATTACHMENT_FETCH_FAILED = "F05_ATTACHMENT_FETCH_FAILED"
    F06_MANUAL_ROUTING = "F06_MANUAL_ROUTING"
    F07_CLINIC_PMS_AUTH_FAILED = "F07_CLINIC_PMS_AUTH_FAILED"
    F08_CLINIC_PMS_TIMEOUT = "F08_CLINIC_PMS_TIMEOUT"
    F09_CLINIC_IMPORT_REJECTED = "F09_CLINIC_IMPORT_REJECTED"
    F10_DELIVERED_NOT_ACTIONED = "F10_DELIVERED_NOT_ACTIONED"
    F11_RESPONSE_NOT_FILED = "F11_RESPONSE_NOT_FILED"


# --- Shared by both systems -------------------------------------------------------------


class ActiveStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class AuthStatus(StrEnum):
    """State of a PMS connection's credentials."""

    VALID = "VALID"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class FetchStatus(StrEnum):
    """Outcome of downloading a document or file."""

    PENDING = "PENDING"
    FETCHED = "FETCHED"
    FAILED = "FAILED"


# --- Composer (GP side) -----------------------------------------------------------------


class GpPmsVendor(StrEnum):
    KAURI_PMS = "KAURI_PMS"
    HARBOUR_PMS = "HARBOUR_PMS"
    SUMMIT_PMS = "SUMMIT_PMS"


class GpUserRole(StrEnum):
    GP = "GP"
    NURSE_PRACTITIONER = "NURSE_PRACTITIONER"


class ReferralStatus(StrEnum):
    """Composer's view of a referral."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    GENERATED = "GENERATED"
    SENT = "SENT"
    RECEIVED = "RECEIVED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DECLINED = "DECLINED"
    ERROR = "ERROR"


class ErrorLevel(StrEnum):
    NONE = "NONE"
    NON_CRITICAL = "NON_CRITICAL"
    CRITICAL = "CRITICAL"


class EventLevel(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class ResponseType(StrEnum):
    """Specialist responses that travel back to the GP (the return leg)."""

    VIEWED = "VIEWED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DECLINED = "DECLINED"
    MESSAGE = "MESSAGE"


class InboxFilingStatus(StrEnum):
    """Whether a response was filed into the GP PMS inbox."""

    PENDING = "PENDING"
    FILED = "FILED"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"  # The practice chose not to receive this type of response.


# --- Hub --------------------------------------------------------------------------------


class ClinicPmsVendor(StrEnum):
    KAURI_PMS = "KAURI_PMS"
    TASMAN_CLINIC = "TASMAN_CLINIC"
    RIDGELINE = "RIDGELINE"


class EndpointStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class RoutingMode(StrEnum):
    ELECTRONIC = "ELECTRONIC"  # The clinic has an active account; deliver to every channel.
    MANUAL = "MANUAL"  # No active account; nothing is delivered electronically.


class HubReferralStatus(StrEnum):
    """The Hub's (specialist-facing) view of a referral."""

    NEW = "NEW"
    VIEWED = "VIEWED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DECLINED = "DECLINED"
    CANCELLED = "CANCELLED"


class HubFileKind(StrEnum):
    PDF = "PDF"
    ATTACHMENTS = "ATTACHMENTS"


class DeliveryChannel(StrEnum):
    EMAIL = "EMAIL"
    PORTAL = "PORTAL"
    CLINIC_PMS = "CLINIC_PMS"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class DeliveryErrorCode(StrEnum):
    """Error codes for failed deliveries and attempts. Free text in SQL, so no CHECK list."""

    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    TIMEOUT = "TIMEOUT"
    PATIENT_MISMATCH = "PATIENT_MISMATCH"
    PATIENT_INCOMPLETE = "PATIENT_INCOMPLETE"


class RecipientActionType(StrEnum):
    VIEWED = "VIEWED"
    DOWNLOADED = "DOWNLOADED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DECLINED = "DECLINED"
    MESSAGE = "MESSAGE"


class CallbackStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


# --- Composer events ------------------------------------------------------------------------

# The leg each Composer timeline event belongs to (DESIGN.md section 3.2). The simulator logs
# events under these legs, and the MCP timeline labels them the same way.
COMPOSER_EVENT_LEG: dict[str, str] = {
    "SUBMITTED": "compose",
    "VALIDATION_FAILED": "compose",
    "SOURCE_DOCUMENTS_FETCHED": "compose",
    "PDF_GENERATED": "compose",
    "PDF_TOO_LARGE": "compose",
    "PDF_COMPRESSION_RETRY": "compose",
    "PDF_GENERATION_FAILED": "compose",
    "FILED_TO_OUTBOX": "compose",
    "SENT_TO_HUB": "ingest",
    "HUB_SEND_FAILED": "ingest",
    "AUTO_RESEND": "ingest",
    "CHECK_RECEIVED": "ingest",
    "HUB_ACCEPTED": "ingest",
    "RESPONSE_RECEIVED": "return",
    "RESPONSE_FILED": "return",
    "RESPONSE_FILING_FAILED": "return",
}


class KnowledgeSource(StrEnum):
    """Where a knowledge chunk comes from (DESIGN.md section 7.3)."""

    TICKET = "TICKET"
    RUNBOOK = "RUNBOOK"


# --- Record ids ---------------------------------------------------------------------------

# Every id carries its table's prefix, so a bare id in evidence or a log line says what it
# is. Referrals use the human-facing `GR-`/`HR-` forms; everything else is `prefix_digits`.
ID_PREFIX: dict[str, str] = {
    "composer.gp_practice": "prc_",
    "composer.gp_user": "gpu_",
    "composer.pms_connection": "pmc_",
    "composer.patient_snapshot": "pt_",
    "composer.referral": "GR-",
    "composer.referral_event": "rev_",
    "composer.source_document": "doc_",
    "composer.hub_send": "hs_",
    "composer.inbound_response": "ir_",
    "hub.clinic": "cln_",
    "hub.specialist": "spc_",
    "hub.clinic_integration": "ci_",
    "hub.hub_referral": "HR-",
    "hub.hub_file": "hf_",
    "hub.delivery": "dl_",
    "hub.delivery_attempt": "da_",
    "hub.recipient_action": "ra_",
    "hub.return_callback": "rc_",
    "knowledge.support_ticket": "tkt_",
    "knowledge.runbook": "rb_",
}

_ID = re.compile(r"(?P<prefix>[A-Za-z]+[_-])[0-9]+")
_TABLE_BY_PREFIX = {prefix: table for table, prefix in ID_PREFIX.items()}


def table_for_id(record_id: str) -> str | None:
    """The table a well-formed id belongs to, or None if the id is unknown or malformed."""
    match = _ID.fullmatch(record_id)
    if match is None:
        return None
    return _TABLE_BY_PREFIX.get(match.group("prefix"))
