"""Find every row linked to one referral, across both systems, and match evidence specs.

Used by scenario tests now, and by evaluation (T3.3) to turn a scenario's evidence spec into
the concrete record ids an answer should cite.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

    from sr_simulator.scenario import EvidenceSpec, Scalar


@dataclass(frozen=True)
class CaseRows:
    """Rows linked to one Composer referral. Lists are empty when nothing is linked."""

    referral: Referral
    gp_practices: list[GpPractice]
    gp_users: list[GpUser]
    pms_connections: list[PmsConnection]
    patients: list[PatientSnapshot]
    referral_events: list[ReferralEvent]
    source_documents: list[SourceDocument]
    hub_sends: list[HubSend]
    inbound_responses: list[InboundResponse]
    clinics: list[Clinic]
    specialists: list[Specialist]
    clinic_integrations: list[ClinicIntegration]
    hub_referrals: list[HubReferral]
    hub_files: list[HubFile]
    deliveries: list[Delivery]
    delivery_attempts: list[DeliveryAttempt]
    recipient_actions: list[RecipientAction]
    return_callbacks: list[ReturnCallback]

    def tables(self) -> dict[str, Sequence[DataclassInstance]]:
        return {
            "composer.referral": [self.referral],
            "composer.gp_practice": self.gp_practices,
            "composer.gp_user": self.gp_users,
            "composer.pms_connection": self.pms_connections,
            "composer.patient_snapshot": self.patients,
            "composer.referral_event": self.referral_events,
            "composer.source_document": self.source_documents,
            "composer.hub_send": self.hub_sends,
            "composer.inbound_response": self.inbound_responses,
            "hub.clinic": self.clinics,
            "hub.specialist": self.specialists,
            "hub.clinic_integration": self.clinic_integrations,
            "hub.hub_referral": self.hub_referrals,
            "hub.hub_file": self.hub_files,
            "hub.delivery": self.deliveries,
            "hub.delivery_attempt": self.delivery_attempts,
            "hub.recipient_action": self.recipient_actions,
            "hub.return_callback": self.return_callbacks,
        }


def case_rows(world: World, referral_id: str) -> CaseRows:
    """Rows linked to the Composer referral `referral_id`, across both systems."""
    referral = next(r for r in world.referrals if r.id == referral_id)
    hub_id = referral.hub_referral_id
    clinic_id = referral.recipient_clinic_ref
    deliveries = [d for d in world.deliveries if d.hub_referral_id == hub_id]
    delivery_ids = {d.id for d in deliveries}
    return CaseRows(
        referral=referral,
        gp_practices=[p for p in world.gp_practices if p.id == referral.practice_id],
        gp_users=[u for u in world.gp_users if u.id == referral.gp_user_id],
        pms_connections=[c for c in world.pms_connections if c.practice_id == referral.practice_id],
        patients=[p for p in world.patients if p.id == referral.patient_id],
        referral_events=[e for e in world.referral_events if e.referral_id == referral_id],
        source_documents=[d for d in world.source_documents if d.referral_id == referral_id],
        hub_sends=[s for s in world.hub_sends if s.referral_id == referral_id],
        inbound_responses=[i for i in world.inbound_responses if i.referral_id == referral_id],
        clinics=[c for c in world.clinics if c.id == clinic_id],
        specialists=[s for s in world.specialists if s.id == referral.recipient_specialist_ref],
        clinic_integrations=[i for i in world.clinic_integrations if i.clinic_id == clinic_id],
        hub_referrals=[h for h in world.hub_referrals if h.id == hub_id],
        hub_files=[f for f in world.hub_files if f.hub_referral_id == hub_id],
        deliveries=deliveries,
        delivery_attempts=[a for a in world.delivery_attempts if a.delivery_id in delivery_ids],
        recipient_actions=[a for a in world.recipient_actions if a.hub_referral_id == hub_id],
        return_callbacks=[c for c in world.return_callbacks if c.hub_referral_id == hub_id],
    )


def _plain(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def matches(row: DataclassInstance, where: Mapping[str, Scalar | list[Scalar]]) -> bool:
    for column, expected in where.items():
        actual = _plain(getattr(row, column))
        allowed = expected if isinstance(expected, list) else [expected]
        if actual not in allowed:
            return False
    return True


def find_evidence(rows: CaseRows, spec: EvidenceSpec) -> list[DataclassInstance]:
    return [row for row in rows.tables().get(spec.table, []) if matches(row, spec.where)]
