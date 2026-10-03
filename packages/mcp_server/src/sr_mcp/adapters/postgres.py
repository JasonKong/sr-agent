"""ReferralStore over Postgres, connected as a member of the read-only sr_reader role."""

from __future__ import annotations

from psycopg_pool import ConnectionPool

from sr_mcp.adapters.base import PatientFields
from sr_mcp.masking import FieldStatus, patient_ref


class PostgresReferralStore:
    def __init__(self, url: str, *, mask_salt: str, max_connections: int = 4) -> None:
        self._salt = mask_salt
        self._pool = ConnectionPool(
            url, min_size=1, max_size=max_connections, kwargs={"autocommit": True}, open=True
        )

    def close(self) -> None:
        self._pool.close()

    def patient_fields(self, patient_id: str) -> PatientFields | None:
        # The reader role cannot select patient values; this function returns status only.
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT synthetic_nhi, date_of_birth FROM composer.patient_field_status(%s)",
                (patient_id,),
            ).fetchone()
        if row is None:
            return None
        return PatientFields(
            patient_ref=patient_ref(patient_id, self._salt),
            synthetic_nhi=FieldStatus(row[0]),
            date_of_birth=FieldStatus(row[1]),
        )
