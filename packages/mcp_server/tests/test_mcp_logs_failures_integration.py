"""Contract tests for the JSONL logs and list_failed_referrals, against the simulator."""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest

from sr_db.domain import FailureCode
from sr_mcp.adapters.base import LogQuery
from sr_mcp.adapters.jsonl_logs import JsonlLogSource
from sr_mcp.adapters.postgres import PostgresReferralStore
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.logs import write_logs
from sr_simulator.rows import World
from sr_simulator.write import write_world

pytestmark = pytest.mark.integration

WORLD = SimConfig(
    seed=19, practices=8, clinics=14, referrals=600, failure_rate=0.2, distractor_rate=0.1
)
# Where each failure code is recorded; codes not listed are silent and never appear.
EXPECTED_LEG = {
    FailureCode.F01_MISSING_REQUIRED_FIELD: "compose",
    FailureCode.F02_PDF_TOO_LARGE: "compose",
    FailureCode.F04_HUB_UNAVAILABLE: "ingest",
    FailureCode.F05_ATTACHMENT_FETCH_FAILED: "ingest",
    FailureCode.F07_CLINIC_PMS_AUTH_FAILED: "deliver",
    FailureCode.F08_CLINIC_PMS_TIMEOUT: "deliver",
    FailureCode.F09_CLINIC_IMPORT_REJECTED: "deliver",
    FailureCode.F11_RESPONSE_NOT_FILED: "return",
}


@pytest.fixture(scope="module")
def world() -> World:
    return generate(WORLD)


@pytest.fixture
def logs(world: World, tmp_path: Path) -> JsonlLogSource:
    write_logs(world, tmp_path)
    return JsonlLogSource(tmp_path)


@pytest.fixture
def store(
    migrated: psycopg.Connection, reader_url: str, world: World
) -> Iterator[PostgresReferralStore]:
    write_world(migrated, world)
    store = PostgresReferralStore(reader_url, mask_salt="contract")
    yield store
    store.close()


# --- JSONL logs --------------------------------------------------------------------------------


def test_logs_by_correlation_match_the_simulator(logs: JsonlLogSource, world: World) -> None:
    for referral in world.referrals[::40]:
        found = logs.search(LogQuery(correlation_id=referral.correlation_id, limit=500))
        expected = [line for line in world.logs if line.correlation_id == referral.correlation_id]
        assert {line.id for line in found} == {line.id for line in expected}
        assert [line.timestamp for line in found] == sorted(
            (line.timestamp for line in found), reverse=True
        )
    assert logs.skipped_lines == 0


def test_f07_auth_error_is_only_in_the_logs(logs: JsonlLogSource, world: World) -> None:
    rid = sorted(r for r, c in world.labels.items() if c is FailureCode.F07_CLINIC_PMS_AUTH_FAILED)[
        0
    ]
    corr = next(r.correlation_id for r in world.referrals if r.id == rid)
    errors = logs.search(LogQuery(correlation_id=corr, min_level="ERROR"))
    assert [line.event for line in errors] == ["CLINIC_PMS_AUTH_FAILED"]
    assert errors[0].error_code == "AUTH_TOKEN_EXPIRED"


# --- list_failed_referrals ---------------------------------------------------------------------


def test_data_now_is_the_latest_time_in_the_data(
    store: PostgresReferralStore, world: World
) -> None:
    latest = max(
        [e.occurred_at for e in world.referral_events]
        + [d.updated_at for d in world.deliveries]
        + [a.occurred_at for a in world.recipient_actions]
    )
    assert store.data_now() == latest
    assert latest <= WORLD.anchor_time


def _all_failures(store: PostgresReferralStore) -> dict[str, set[str]]:
    since = store.data_now() - timedelta(days=WORLD.history_days + 5)
    legs: dict[str, set[str]] = {}
    for failure in store.failed_referrals(since, None, 10_000):
        assert failure.composer_id is not None
        legs.setdefault(failure.composer_id, set()).add(failure.leg)
    return legs


def test_every_recorded_failure_is_listed_on_its_leg(
    store: PostgresReferralStore, world: World
) -> None:
    listed = _all_failures(store)
    for referral_id, code in world.labels.items():
        if code in EXPECTED_LEG:
            assert EXPECTED_LEG[code] in listed.get(referral_id, set()), (referral_id, code)


def test_silent_problems_and_recovered_distractors_are_not_listed(
    store: PostgresReferralStore, world: World
) -> None:
    listed = set(_all_failures(store))
    for referral in world.referrals:
        code = world.labels.get(referral.id, FailureCode.F00_NO_FAULT)
        if code not in EXPECTED_LEG:  # F00 (including distractors), F03, F06, F10
            assert referral.id not in listed, (
                referral.id,
                code,
                world.distractors.get(referral.id),
            )


def test_window_leg_filter_order_and_reasons(store: PostgresReferralStore) -> None:
    now = store.data_now()
    week = store.failed_referrals(now - timedelta(days=7), None, 500)
    assert week
    assert all(f.failed_at >= now - timedelta(days=7) for f in week)
    assert [f.failed_at for f in week] == sorted((f.failed_at for f in week), reverse=True)
    deliver = store.failed_referrals(now - timedelta(days=90), "deliver", 500)
    assert deliver
    assert {f.leg for f in deliver} == {"deliver"}
    assert all("failed" in f.reason for f in deliver)
    assert len(store.failed_referrals(now - timedelta(days=90), None, 5)) == 5
