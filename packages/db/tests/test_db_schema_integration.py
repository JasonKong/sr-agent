"""Integration tests for the composer, hub and knowledge schemas (DESIGN.md section 3.3).

Each test runs against a throwaway database with every migration applied.
"""

from enum import StrEnum

import psycopg
import pytest
from psycopg import errors, sql

from sr_db.domain import (
    ID_PREFIX,
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

pytestmark = pytest.mark.integration

# One valid row per table, linked into a complete chain in each system.
VALID_ROWS = """
INSERT INTO composer.gp_practice (id, name, pms_vendor, status, created_at)
VALUES ('prc_1', 'Totara Street Medical', 'KAURI_PMS', 'ACTIVE', '2026-09-01T09:00:00Z');
INSERT INTO composer.gp_user (id, practice_id, name, role)
VALUES ('gpu_1', 'prc_1', 'Dr Aroha Test', 'GP');
INSERT INTO composer.pms_connection (id, practice_id, auth_status, token_expires_at, updated_at)
VALUES ('pmc_1', 'prc_1', 'VALID', '2026-12-01T00:00:00Z', '2026-09-01T09:00:00Z');
INSERT INTO composer.patient_snapshot
    (id, practice_id, synthetic_nhi, given_name, family_name, date_of_birth)
VALUES ('pt_1', 'prc_1', 'ZZZ0001', 'Test', 'Patient', '01-02-1980');
INSERT INTO composer.referral (
    id, practice_id, gp_user_id, patient_id, recipient_clinic_ref, recipient_specialist_ref,
    correlation_id, status, error_level, hub_referral_id, pdf_size_bytes,
    created_at, submitted_at, sent_at)
VALUES ('GR-10001', 'prc_1', 'gpu_1', 'pt_1', 'cln_1', 'spc_1',
    'corr-0001', 'SENT', 'NONE', 'HR-50001', 250000,
    '2026-10-01T09:00:00Z', '2026-10-01T09:05:00Z', '2026-10-01T09:06:00Z');
INSERT INTO composer.referral_event (id, referral_id, event, sub_type, level, detail, occurred_at)
VALUES ('rev_1', 'GR-10001', 'SENT_TO_HUB', NULL, 'INFO', 'Accepted by Hub',
    '2026-10-01T09:06:00Z');
INSERT INTO composer.source_document
    (id, referral_id, title, size_bytes, fetch_status, fetch_error)
VALUES ('doc_1', 'GR-10001', 'Recent bloods', 120000, 'FETCHED', NULL);
INSERT INTO composer.hub_send
    (id, referral_id, attempt_no, http_status, error_message, attempted_at)
VALUES ('hs_1', 'GR-10001', 1, 201, NULL, '2026-10-01T09:06:00Z');
INSERT INTO composer.inbound_response (
    id, referral_id, hub_action_ref, type, received_at, inbox_filing_status, filing_error)
VALUES ('ir_1', 'GR-10001', 'ra_1', 'ACKNOWLEDGED', '2026-10-02T10:00:00Z', 'FILED', NULL);

INSERT INTO hub.clinic (id, name, specialty_group, status, has_active_account, created_at)
VALUES ('cln_1', 'Kowhai Orthopaedics', 'ORTHOPAEDICS', 'ACTIVE', true, '2026-01-01T00:00:00Z');
INSERT INTO hub.specialist (id, clinic_id, name, status)
VALUES ('spc_1', 'cln_1', 'Dr Rangi Test', 'ACTIVE');
INSERT INTO hub.clinic_integration
    (id, clinic_id, vendor, auth_status, token_expires_at, endpoint_status, updated_at)
VALUES ('ci_1', 'cln_1', 'TASMAN_CLINIC', 'VALID', '2026-12-01T00:00:00Z', 'HEALTHY',
    '2026-09-01T00:00:00Z');
INSERT INTO hub.hub_referral (
    id, source_ref, correlation_id, clinic_id, specialist_id, routing_mode, status,
    created_at, accepted_at)
VALUES ('HR-50001', 'GR-10001', 'corr-0001', 'cln_1', 'spc_1', 'ELECTRONIC', 'ACKNOWLEDGED',
    '2026-10-01T09:06:00Z', '2026-10-01T09:06:30Z');
INSERT INTO hub.hub_file
    (id, hub_referral_id, kind, fetch_status, size_bytes, fetch_error, updated_at)
VALUES ('hf_1', 'HR-50001', 'PDF', 'FETCHED', 250000, NULL, '2026-10-01T09:06:20Z');
INSERT INTO hub.delivery (
    id, hub_referral_id, channel, target_ref, status, last_error_code, updated_at)
VALUES ('dl_1', 'HR-50001', 'CLINIC_PMS', 'ci_1', 'SUCCEEDED', NULL, '2026-10-01T09:07:00Z');
INSERT INTO hub.delivery_attempt (
    id, delivery_id, attempt_no, http_status, error_code, error_message, latency_ms, attempted_at)
VALUES ('da_1', 'dl_1', 1, 200, NULL, NULL, 840, '2026-10-01T09:07:00Z');
INSERT INTO hub.recipient_action (id, hub_referral_id, action, actor_ref, occurred_at)
VALUES ('ra_1', 'HR-50001', 'ACKNOWLEDGED', 'spc_1', '2026-10-02T10:00:00Z');
INSERT INTO hub.return_callback
    (id, hub_referral_id, action_id, attempt_no, http_status, status, attempted_at)
VALUES ('rc_1', 'HR-50001', 'ra_1', 1, 200, 'SUCCEEDED', '2026-10-02T10:00:05Z');

INSERT INTO knowledge.support_ticket (
    id, referral_ref, clinic_ref, title, description, resolution, root_cause_code,
    created_at, resolved_at)
VALUES ('tkt_1', 'GR-10001', 'cln_1', 'Specialist says not received', 'GP called...',
    'Clinic re-authorised its PMS', 'F07_CLINIC_PMS_AUTH_FAILED',
    '2026-08-01T09:00:00Z', '2026-08-01T11:00:00Z');
INSERT INTO knowledge.runbook (id, slug, error_code, title, body_markdown, updated_at)
VALUES ('rb_1', 'clinic-pms-auth-failed', 'F07_CLINIC_PMS_AUTH_FAILED',
    'Clinic PMS authorisation failed', '# Steps', '2026-08-01T00:00:00Z');
"""

# Every column restricted by a CHECK (... IN ...) list, and the enum that defines its values.
ENUM_COLUMNS: list[tuple[str, str, type[StrEnum]]] = [
    ("composer.gp_practice", "pms_vendor", GpPmsVendor),
    ("composer.gp_practice", "status", ActiveStatus),
    ("composer.gp_user", "role", GpUserRole),
    ("composer.pms_connection", "auth_status", AuthStatus),
    ("composer.referral", "status", ReferralStatus),
    ("composer.referral", "error_level", ErrorLevel),
    ("composer.referral_event", "level", EventLevel),
    ("composer.source_document", "fetch_status", FetchStatus),
    ("composer.inbound_response", "type", ResponseType),
    ("composer.inbound_response", "inbox_filing_status", InboxFilingStatus),
    ("hub.clinic", "status", ActiveStatus),
    ("hub.specialist", "status", ActiveStatus),
    ("hub.clinic_integration", "vendor", ClinicPmsVendor),
    ("hub.clinic_integration", "auth_status", AuthStatus),
    ("hub.clinic_integration", "endpoint_status", EndpointStatus),
    ("hub.hub_referral", "routing_mode", RoutingMode),
    ("hub.hub_referral", "status", HubReferralStatus),
    ("hub.hub_file", "kind", HubFileKind),
    ("hub.hub_file", "fetch_status", FetchStatus),
    ("hub.delivery", "channel", DeliveryChannel),
    ("hub.delivery", "status", DeliveryStatus),
    ("hub.recipient_action", "action", RecipientActionType),
    ("hub.return_callback", "status", CallbackStatus),
    ("knowledge.support_ticket", "root_cause_code", FailureCode),
    ("knowledge.runbook", "error_code", FailureCode),
]


def _ident(table: str) -> sql.Identifier:
    schema, name = table.split(".")
    return sql.Identifier(schema, name)


def _first_id(conn: psycopg.Connection, table: str) -> str:
    row = conn.execute(sql.SQL("SELECT id FROM {} LIMIT 1").format(_ident(table))).fetchone()
    assert row is not None
    return str(row[0])


@pytest.fixture
def db(migrated: psycopg.Connection) -> psycopg.Connection:
    migrated.execute(VALID_ROWS.encode())
    return migrated


def test_a_complete_valid_chain_fits_every_table(db: psycopg.Connection) -> None:
    for table in ID_PREFIX:
        row = db.execute(sql.SQL("SELECT count(*) FROM {}").format(_ident(table))).fetchone()
        assert row == (1,), table


@pytest.mark.parametrize(
    ("table", "column", "enum"), ENUM_COLUMNS, ids=lambda v: v if isinstance(v, str) else ""
)
def test_every_enum_value_is_accepted(
    db: psycopg.Connection, table: str, column: str, enum: type[StrEnum]
) -> None:
    row_id = _first_id(db, table)
    for member in enum:
        with db.transaction(force_rollback=True):
            db.execute(
                sql.SQL("UPDATE {} SET {} = %s WHERE id = %s").format(
                    _ident(table), sql.Identifier(column)
                ),
                (member.value, row_id),
            )


@pytest.mark.parametrize(
    ("table", "column", "enum"), ENUM_COLUMNS, ids=lambda v: v if isinstance(v, str) else ""
)
def test_values_outside_the_enum_are_rejected(
    db: psycopg.Connection, table: str, column: str, enum: type[StrEnum]
) -> None:
    row_id = _first_id(db, table)
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            sql.SQL("UPDATE {} SET {} = 'NOT_A_REAL_VALUE' WHERE id = %s").format(
                _ident(table), sql.Identifier(column)
            ),
            (row_id,),
        )


def test_every_value_list_check_is_covered_by_a_domain_enum(migrated: psycopg.Connection) -> None:
    rows = migrated.execute(
        """
        SELECT n.nspname || '.' || c.relname, a.attname
        FROM pg_constraint k
        JOIN pg_class c ON c.oid = k.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY (k.conkey)
        WHERE k.contype = 'c'
          AND n.nspname IN ('composer', 'hub', 'knowledge')
          AND pg_get_constraintdef(k.oid) LIKE '%ANY (ARRAY[%'
        """
    ).fetchall()

    assert set(rows) == {(table, column) for table, column, _ in ENUM_COLUMNS}


@pytest.mark.parametrize("table", list(ID_PREFIX))
def test_ids_must_use_the_table_prefix(db: psycopg.Connection, table: str) -> None:
    row_id = _first_id(db, table)
    with pytest.raises(errors.CheckViolation), db.transaction():
        db.execute(
            sql.SQL("UPDATE {} SET id = 'wrong_1' WHERE id = %s").format(_ident(table)),
            (row_id,),
        )


def test_cross_system_references_are_plain_ids_not_foreign_keys(db: psycopg.Connection) -> None:
    # Real systems lose track of each other (for example F04: the Hub never got the referral),
    # so the link between them must be able to dangle.
    db.execute(
        "UPDATE composer.referral"
        " SET hub_referral_id = 'HR-99999', recipient_clinic_ref = 'cln_404'"
    )
    db.execute("UPDATE hub.hub_referral SET source_ref = 'GR-99999'")
    db.execute("UPDATE composer.inbound_response SET hub_action_ref = 'ra_404'")

    cross = db.execute(
        """
        SELECT count(*) FROM pg_constraint k
        JOIN pg_class src ON src.oid = k.conrelid
        JOIN pg_class dst ON dst.oid = k.confrelid
        WHERE k.contype = 'f' AND src.relnamespace <> dst.relnamespace
        """
    ).fetchone()
    assert cross == (0,)


def test_foreign_keys_within_a_system_are_enforced(db: psycopg.Connection) -> None:
    with pytest.raises(errors.ForeignKeyViolation), db.transaction():
        db.execute(
            "INSERT INTO hub.delivery"
            " (id, hub_referral_id, channel, target_ref, status, updated_at)"
            " VALUES ('dl_2', 'HR-99999', 'EMAIL', 'clinic-inbox', 'PENDING', now())"
        )


def test_every_foreign_key_column_is_indexed(migrated: psycopg.Connection) -> None:
    unindexed = migrated.execute(
        """
        SELECT k.conrelid::regclass::text, a.attname
        FROM pg_constraint k
        JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
        JOIN pg_namespace n ON n.oid = k.connamespace
        WHERE k.contype = 'f'
          AND n.nspname IN ('composer', 'hub', 'knowledge')
          AND NOT EXISTS (
              SELECT 1 FROM pg_index i
              WHERE i.indrelid = k.conrelid AND i.indkey[0] = k.conkey[1]
          )
        """
    ).fetchall()

    assert unindexed == []


def test_date_of_birth_keeps_raw_and_malformed_values(db: psycopg.Connection) -> None:
    # F01 needs a malformed date of birth exactly as the GP PMS sent it.
    db.execute("UPDATE composer.patient_snapshot SET date_of_birth = '31/02/80'")
    db.execute("UPDATE composer.patient_snapshot SET date_of_birth = NULL, synthetic_nhi = NULL")


def test_attempt_numbers_are_unique_per_parent(db: psycopg.Connection) -> None:
    with pytest.raises(errors.UniqueViolation), db.transaction():
        db.execute(
            "INSERT INTO hub.delivery_attempt (id, delivery_id, attempt_no, attempted_at)"
            " VALUES ('da_2', 'dl_1', 1, now())"
        )
