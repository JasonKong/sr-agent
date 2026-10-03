"""ReferralStore over Postgres, connected as a member of the read-only sr_reader role.

Sessions run in UTC so every time a tool returns is UTC, like the logs.
"""

from __future__ import annotations

from datetime import datetime

from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from sr_db.domain import COMPOSER_EVENT_LEG
from sr_mcp.adapters.base import (
    ClinicInfo,
    ComposerSide,
    HubSide,
    PatientFields,
    PersonInfo,
    PracticeInfo,
    Recipient,
    ReferralFilters,
    ReferralSummary,
    ReferralView,
    TimelineEntry,
)
from sr_mcp.masking import FieldStatus, patient_ref

Row = DictRow

_COMPOSER = """
SELECT r.id, r.status, r.error_level, r.created_at, r.submitted_at, r.sent_at,
       r.pdf_size_bytes, r.correlation_id, r.patient_id, r.recipient_clinic_ref,
       r.recipient_specialist_ref, r.hub_referral_id,
       p.id AS practice_id, p.name AS practice_name, p.pms_vendor,
       u.id AS gp_user_id, u.name AS gp_user_name, u.role AS gp_user_role
FROM composer.referral r
JOIN composer.gp_practice p ON p.id = r.practice_id
JOIN composer.gp_user u ON u.id = r.gp_user_id
"""
_HUB = """
SELECT h.id, h.status, h.routing_mode, h.created_at, h.accepted_at, h.clinic_id,
       h.specialist_id, h.correlation_id, h.source_ref
FROM hub.hub_referral h
"""

_FIND = """
SELECT * FROM (
    SELECT r.id AS composer_id, h.id AS hub_id, r.created_at, r.status AS composer_status,
           h.status AS hub_status, r.error_level, c.name AS clinic_name,
           p.name AS practice_name, r.correlation_id,
           (r.error_level <> 'NONE'
            OR EXISTS (SELECT 1 FROM hub.hub_file f
                       WHERE f.hub_referral_id = h.id AND f.fetch_status = 'FAILED')
            OR EXISTS (SELECT 1 FROM hub.delivery d
                       WHERE d.hub_referral_id = h.id AND d.status = 'FAILED')) AS has_error
    FROM composer.referral r
    JOIN composer.gp_practice p ON p.id = r.practice_id
    LEFT JOIN hub.hub_referral h ON h.id = r.hub_referral_id
    LEFT JOIN hub.clinic c ON c.id = r.recipient_clinic_ref
    WHERE (%(clinic)s::text IS NULL OR c.id = %(clinic)s OR c.name ILIKE %(clinic_like)s)
      AND (%(practice)s::text IS NULL OR p.id = %(practice)s OR p.name ILIKE %(practice_like)s)
      AND (%(status)s::text IS NULL OR r.status = %(status)s)
      AND (%(created_from)s::date IS NULL OR r.created_at >= %(created_from)s::date)
      AND (%(created_to)s::date IS NULL OR r.created_at < %(created_to)s::date + 1)
) q
WHERE %(with_errors)s::boolean IS NULL OR q.has_error = %(with_errors)s::boolean
ORDER BY q.created_at DESC, q.composer_id DESC
LIMIT %(limit)s
"""


def _like(text: str | None) -> str | None:
    if text is None:
        return None
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class PostgresReferralStore:
    def __init__(self, url: str, *, mask_salt: str, max_connections: int = 4) -> None:
        self._salt = mask_salt
        self._pool: ConnectionPool[Connection[DictRow]] = ConnectionPool(
            url,
            connection_class=Connection[DictRow],
            min_size=1,
            max_size=max_connections,
            kwargs={"autocommit": True, "options": "-c timezone=UTC", "row_factory": dict_row},
            open=True,
        )

    def close(self) -> None:
        self._pool.close()

    # --- Patients -----------------------------------------------------------------------------

    def patient_fields(self, patient_id: str) -> PatientFields | None:
        with self._pool.connection() as conn:
            return self._patient_fields(conn, patient_id)

    def _patient_fields(self, conn: Connection[DictRow], patient_id: str) -> PatientFields | None:
        # The reader role cannot select patient values; this function returns status only.
        row: Row | None = conn.execute(
            "SELECT synthetic_nhi, date_of_birth FROM composer.patient_field_status(%s)",
            (patient_id,),
        ).fetchone()
        if row is None:
            return None
        return PatientFields(
            patient_ref=patient_ref(patient_id, self._salt),
            synthetic_nhi=FieldStatus(row["synthetic_nhi"]),
            date_of_birth=FieldStatus(row["date_of_birth"]),
        )

    # --- Resolving an id to both sides ---------------------------------------------------------

    def _resolve(
        self, conn: Connection[DictRow], referral_id: str
    ) -> tuple[Row | None, Row | None]:
        composer: Row | None
        hub: Row | None
        if referral_id.startswith("GR-"):
            composer = conn.execute(_COMPOSER + "WHERE r.id = %s", (referral_id,)).fetchone()
            hub = None
            if composer is not None:
                hub = conn.execute(
                    _HUB + "WHERE h.id = %s OR h.source_ref = %s ORDER BY h.id = %s DESC LIMIT 1",
                    (composer["hub_referral_id"], referral_id, composer["hub_referral_id"]),
                ).fetchone()
        else:
            hub = conn.execute(_HUB + "WHERE h.id = %s", (referral_id,)).fetchone()
            composer = None
            if hub is not None:
                composer = conn.execute(
                    _COMPOSER + "WHERE r.id = %s", (hub["source_ref"],)
                ).fetchone()
        return composer, hub

    # --- get_referral ----------------------------------------------------------------------------

    def get_referral(self, referral_id: str) -> ReferralView | None:
        with self._pool.connection() as conn:
            composer, hub = self._resolve(conn, referral_id)
            if composer is None and hub is None:
                return None
            source = composer if composer is not None else hub
            assert source is not None
            if composer is not None:
                clinic_id = composer["recipient_clinic_ref"]
                specialist_id = composer["recipient_specialist_ref"]
            else:
                assert hub is not None
                clinic_id, specialist_id = hub["clinic_id"], hub["specialist_id"]
            clinic = conn.execute(
                "SELECT id, name, specialty_group, status, has_active_account FROM hub.clinic"
                " WHERE id = %s",
                (clinic_id,),
            ).fetchone()
            specialist = conn.execute(
                "SELECT id, name FROM hub.specialist WHERE id = %s", (specialist_id,)
            ).fetchone()
            patient = self._patient_fields(conn, composer["patient_id"]) if composer else None

        composer_side = None
        if composer is not None:
            assert patient is not None
            composer_side = ComposerSide(
                id=composer["id"],
                status=composer["status"],
                error_level=composer["error_level"],
                created_at=composer["created_at"],
                submitted_at=composer["submitted_at"],
                sent_at=composer["sent_at"],
                pdf_size_bytes=composer["pdf_size_bytes"],
                practice=PracticeInfo(
                    id=composer["practice_id"],
                    name=composer["practice_name"],
                    pms_vendor=composer["pms_vendor"],
                ),
                gp_user=PersonInfo(
                    id=composer["gp_user_id"],
                    name=composer["gp_user_name"],
                    role=composer["gp_user_role"],
                ),
                patient=patient,
            )
        hub_side = None
        if hub is not None:
            hub_side = HubSide(
                id=hub["id"],
                status=hub["status"],
                routing_mode=hub["routing_mode"],
                created_at=hub["created_at"],
                accepted_at=hub["accepted_at"],
            )
        link = "linked" if composer and hub else ("composer_only" if composer else "hub_only")
        return ReferralView(
            link=link,
            correlation_id=source["correlation_id"],
            composer=composer_side,
            hub=hub_side,
            recipient=Recipient(
                clinic=ClinicInfo(**clinic) if clinic else None,
                specialist=PersonInfo(**specialist) if specialist else None,
            ),
        )

    # --- find_referrals ---------------------------------------------------------------------------

    def find_referrals(self, filters: ReferralFilters) -> list[ReferralSummary]:
        params = {
            "clinic": filters.clinic,
            "clinic_like": _like(filters.clinic),
            "practice": filters.practice,
            "practice_like": _like(filters.practice),
            "status": filters.status,
            "with_errors": filters.with_errors,
            "created_from": filters.created_from,
            "created_to": filters.created_to,
            "limit": filters.limit,
        }
        with self._pool.connection() as conn:
            rows: list[Row] = conn.execute(_FIND, params).fetchall()
        return [ReferralSummary(**row) for row in rows]

    # --- timeline ---------------------------------------------------------------------------------

    def timeline(self, referral_id: str) -> list[TimelineEntry] | None:
        with self._pool.connection() as conn:
            composer, hub = self._resolve(conn, referral_id)
            if composer is None and hub is None:
                return None
            entries: list[TimelineEntry] = []
            if composer is not None:
                entries += self._composer_entries(conn, composer["id"])
            if hub is not None:
                entries += self._hub_entries(conn, hub)
        order = {"composer": 0, "hub": 1}
        return sorted(entries, key=lambda e: (e.at, order[e.system], e.record_id))

    def _composer_entries(self, conn: Connection[DictRow], composer_id: str) -> list[TimelineEntry]:
        rows: list[Row] = conn.execute(
            "SELECT id, event, sub_type, level, detail, occurred_at FROM composer.referral_event"
            " WHERE referral_id = %s",
            (composer_id,),
        ).fetchall()
        return [
            TimelineEntry(
                at=row["occurred_at"],
                system="composer",
                leg=COMPOSER_EVENT_LEG.get(row["event"], "compose"),
                event=row["event"],
                level=row["level"],
                detail=_with_sub_type(row["detail"], row["sub_type"]),
                record_id=row["id"],
            )
            for row in rows
        ]

    def _hub_entries(self, conn: Connection[DictRow], hub: Row) -> list[TimelineEntry]:
        hub_id = hub["id"]

        def entry(
            at: datetime,
            leg: str,
            event: str,
            record_id: str,
            detail: str | None,
            error: bool = False,
        ) -> TimelineEntry:
            level = "ERROR" if error else "INFO"
            return TimelineEntry(
                at=at, system="hub", leg=leg, event=event, level=level, detail=detail,
                record_id=record_id,
            )  # fmt: skip

        entries = [
            entry(hub["created_at"], "ingest", "REFERRAL_RECEIVED", hub_id,
                  f"From Composer referral {hub['source_ref']}")
        ]  # fmt: skip
        file_events = {
            "FETCHED": "FILE_FETCHED",
            "FAILED": "FILE_FETCH_FAILED",
            "PENDING": "FILE_FETCH_PENDING",
        }
        for f in conn.execute(
            "SELECT id, fetch_status, fetch_error, updated_at FROM hub.hub_file"
            " WHERE hub_referral_id = %s",
            (hub_id,),
        ).fetchall():
            failed = f["fetch_status"] == "FAILED"
            entries.append(
                entry(f["updated_at"], "ingest", file_events[f["fetch_status"]], f["id"],
                      f["fetch_error"], failed)
            )  # fmt: skip
        if hub["accepted_at"] is not None:
            entries.append(
                entry(hub["accepted_at"], "ingest", "REFERRAL_ACCEPTED", hub_id,
                      f"Routing {hub['routing_mode']}")
            )  # fmt: skip
        for d in conn.execute(
            "SELECT id, channel, target_ref, status, last_error_code, updated_at FROM hub.delivery"
            " WHERE hub_referral_id = %s",
            (hub_id,),
        ).fetchall():
            detail = f"{d['channel']} to {d['target_ref']}"
            if d["last_error_code"]:
                detail += f"; last error {d['last_error_code']}"
            entries.append(
                entry(d["updated_at"], "deliver", f"DELIVERY_{d['status']}", d["id"], detail,
                      d["status"] == "FAILED")
            )  # fmt: skip
        for a in conn.execute(
            "SELECT id, action, actor_ref, occurred_at FROM hub.recipient_action"
            " WHERE hub_referral_id = %s",
            (hub_id,),
        ).fetchall():
            entries.append(
                entry(a["occurred_at"], "respond", f"RECIPIENT_{a['action']}", a["id"],
                      f"By {a['actor_ref']}")
            )  # fmt: skip
        for c in conn.execute(
            "SELECT c.id, c.status, c.http_status, c.attempted_at, a.action"
            " FROM hub.return_callback c JOIN hub.recipient_action a ON a.id = c.action_id"
            " WHERE c.hub_referral_id = %s",
            (hub_id,),
        ).fetchall():
            ok = c["status"] == "SUCCEEDED"
            entries.append(
                entry(c["attempted_at"], "return", "CALLBACK_SENT" if ok else "CALLBACK_FAILED",
                      c["id"], f"{c['action']} returned to Composer (HTTP {c['http_status']})",
                      not ok)
            )  # fmt: skip
        return entries


def _with_sub_type(detail: str | None, sub_type: str | None) -> str | None:
    if sub_type and detail:
        return f"{detail} ({sub_type})"
    return detail or sub_type
