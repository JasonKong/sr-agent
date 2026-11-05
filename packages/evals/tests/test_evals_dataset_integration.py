"""Every question, in v1 and the holdout set, can be answered through the tools: ids exist,
and names find the referral."""

from datetime import timedelta

import psycopg
import pytest

from sr_evals.dataset import DATASETS, Dataset, load_dataset, world_for
from sr_mcp.adapters.base import ReferralFilters
from sr_mcp.adapters.postgres import PostgresReferralStore
from sr_simulator.rows import World
from sr_simulator.write import write_world

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", params=sorted(DATASETS))
def dataset(request: pytest.FixtureRequest) -> Dataset:
    return load_dataset(DATASETS[request.param])


@pytest.fixture(scope="module")
def world(dataset: Dataset) -> World:
    return world_for(dataset)


def test_named_referrals_resolve_and_names_find_exactly_one(
    migrated: psycopg.Connection, reader_url: str, dataset: Dataset, world: World
) -> None:
    write_world(migrated, world)
    store = PostgresReferralStore(reader_url, mask_salt="evals")
    referrals = {r.id: r for r in world.referrals}
    clinics = {c.id: c.name for c in world.clinics}
    practices = {p.id: p.name for p in world.gp_practices}
    tz = world.referrals[0].created_at.tzinfo
    try:
        for case in dataset.cases:
            named = case.hub_id if case.reference == "hub_id" else None
            view = store.get_referral(named or case.composer_id)
            assert view is not None, case.case_id
            if case.reference != "clinic_and_date":
                continue
            referral = referrals[case.composer_id]
            local_day = referral.created_at.astimezone(tz).date()
            assert f"{local_day.day} {local_day:%B}" in case.question
            # A local (+12/+13) day spans two UTC days: the one before and the day itself.
            found = store.find_referrals(
                ReferralFilters(
                    clinic=clinics[referral.recipient_clinic_ref],
                    practice=practices[referral.practice_id],
                    created_from=local_day - timedelta(days=1),
                    created_to=local_day,
                )
            )
            assert [f.composer_id for f in found] == [case.composer_id], case.case_id
    finally:
        store.close()
