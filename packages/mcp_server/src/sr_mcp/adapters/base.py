"""Adapter interfaces and the records they return (DESIGN.md section 7.2).

Tools only see these interfaces, so a real organisation could swap in its own data
sources (section 11), and unit tests use fakes (`sr_mcp.testing`).
"""

from __future__ import annotations

from datetime import datetime
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


class LogQuery(_Record):
    referral_id: str | None = None
    correlation_id: str | None = None
    service: str | None = None
    level: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 50


class KnowledgeHit(_Record):
    source_type: Literal["ticket", "runbook"]
    source_id: str
    title: str
    text: str
    error_code: str | None
    score: float


class ReferralStore(Protocol):
    """Referral data from both systems. T2.2 and T2.3 add the referral queries."""

    def patient_fields(self, patient_id: str) -> PatientFields | None: ...

    def close(self) -> None: ...


class LogSource(Protocol):
    """Structured logs from both systems: JSONL files locally, CloudWatch in AWS (T2.4)."""

    def search(self, query: LogQuery) -> list[LogRecord]: ...


class KnowledgeSearch(Protocol):
    """Search over past tickets and runbooks (T4.2)."""

    def search(self, query: str, *, error_code: str | None, top_k: int) -> list[KnowledgeHit]: ...
