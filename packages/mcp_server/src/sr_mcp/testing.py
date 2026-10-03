"""In-memory fakes of the adapters, for unit tests (no database, no files)."""

from __future__ import annotations

from datetime import UTC, datetime

from sr_mcp.adapters.base import (
    LEVELS,
    DeliveriesView,
    FailedReferral,
    KnowledgeHit,
    KnowledgeSearch,
    LogQuery,
    LogRecord,
    LogSource,
    PatientFields,
    RecipientView,
    ReferralFilters,
    ReferralStore,
    ReferralSummary,
    ReferralView,
    ReturnPathView,
    TimelineEntry,
)
from sr_mcp.deps import Deps


class FakeReferralStore:
    """Returns canned records. Views and timelines are keyed by every id that should find them."""

    def __init__(
        self,
        patients: dict[str, PatientFields] | None = None,
        views: dict[str, ReferralView] | None = None,
        summaries: list[ReferralSummary] | None = None,
        timelines: dict[str, list[TimelineEntry]] | None = None,
        deliveries: dict[str, DeliveriesView] | None = None,
        recipients: dict[str, RecipientView] | None = None,
        return_paths: dict[str, ReturnPathView] | None = None,
        failures: list[FailedReferral] | None = None,
        now: datetime | None = None,
    ) -> None:
        self.patients = patients or {}
        self.views = views or {}
        self.summaries = summaries or []
        self.timelines = timelines or {}
        self.deliveries_by_id = deliveries or {}
        self.recipients = recipients or {}
        self.return_paths = return_paths or {}
        self.failures = failures or []
        self.now = now or datetime(2026, 10, 1, tzinfo=UTC)
        self.last_filters: ReferralFilters | None = None

    def patient_fields(self, patient_id: str) -> PatientFields | None:
        return self.patients.get(patient_id)

    def get_referral(self, referral_id: str) -> ReferralView | None:
        return self.views.get(referral_id)

    def find_referrals(self, filters: ReferralFilters) -> list[ReferralSummary]:
        self.last_filters = filters
        return self.summaries[: filters.limit]

    def timeline(self, referral_id: str) -> list[TimelineEntry] | None:
        return self.timelines.get(referral_id)

    def deliveries(self, referral_id: str) -> DeliveriesView | None:
        return self.deliveries_by_id.get(referral_id)

    def recipient(self, clinic_id: str) -> RecipientView | None:
        return self.recipients.get(clinic_id)

    def return_path(self, referral_id: str) -> ReturnPathView | None:
        return self.return_paths.get(referral_id)

    def failed_referrals(
        self, since: datetime, leg: str | None, limit: int
    ) -> list[FailedReferral]:
        found = [f for f in self.failures if f.failed_at >= since and leg in (None, f.leg)]
        return sorted(found, key=lambda f: f.failed_at, reverse=True)[:limit]

    def data_now(self) -> datetime:
        return self.now

    def close(self) -> None:
        return None


class FakeLogSource:
    def __init__(self, records: list[LogRecord] | None = None) -> None:
        self.records = records or []

    def search(self, query: LogQuery) -> list[LogRecord]:
        found = [
            r
            for r in self.records
            if query.referral_id in (None, r.referral_id)
            and query.correlation_id in (None, r.correlation_id)
            and query.service in (None, r.service)
            and (query.min_level is None or LEVELS.index(r.level) >= LEVELS.index(query.min_level))
        ]
        return sorted(found, key=lambda r: r.timestamp, reverse=True)[: query.limit]


class FakeKnowledge:
    def __init__(self, hits: list[KnowledgeHit] | None = None) -> None:
        self.hits = hits or []

    def search(self, query: str, *, error_code: str | None, top_k: int) -> list[KnowledgeHit]:
        return [h for h in self.hits if error_code in (None, h.error_code)][:top_k]


def fake_deps(
    *,
    store: ReferralStore | None = None,
    logs: LogSource | None = None,
    knowledge: KnowledgeSearch | None = None,
    max_result_chars: int = 8000,
) -> Deps:
    return Deps(
        store=store or FakeReferralStore(),
        logs=logs or FakeLogSource(),
        knowledge=knowledge or FakeKnowledge(),
        max_result_chars=max_result_chars,
        caller="test",
    )
