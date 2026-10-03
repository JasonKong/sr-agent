"""Unit tests for structured logs (T1.4). No database needed."""

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from sr_db.domain import EventLevel, FailureCode
from sr_simulator.case_rows import case_rows
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.logs import LOG_FIELDS, SERVICES, write_logs
from sr_simulator.rows import World

CONFIG = SimConfig(seed=5, practices=6, clinics=12, referrals=400, failure_rate=0.2)


@pytest.fixture(scope="module")
def world() -> World:
    return generate(CONFIG)


def test_every_line_belongs_to_a_known_service_and_is_well_formed(world: World) -> None:
    assert world.logs
    for line in world.logs:
        assert line.service in SERVICES
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", line.event), line
        assert line.timestamp <= CONFIG.anchor_time, line
        assert line.message, line
        if line.level is EventLevel.ERROR:
            assert line.error_code, line


def test_ids_are_sequential_per_service_in_time_order(world: World) -> None:
    by_service = defaultdict(list)
    for line in world.logs:
        by_service[line.service].append(line)
    for service, lines in by_service.items():
        lines.sort(key=lambda line: line.id)
        assert [line.id for line in lines] == [
            f"{service}:{n:06d}" for n in range(1, len(lines) + 1)
        ]
        assert [line.timestamp for line in lines] == sorted(line.timestamp for line in lines)


def test_each_system_logs_its_own_ids_and_shares_the_correlation_id(world: World) -> None:
    composer = {r.id: r.correlation_id for r in world.referrals}
    hub = {h.id: h.correlation_id for h in world.hub_referrals}
    for line in world.logs:
        ids = composer if line.service.startswith("composer") else hub
        assert ids[line.referral_id] == line.correlation_id, line


def test_every_submitted_referral_and_every_hub_referral_is_logged(world: World) -> None:
    logged = {line.referral_id for line in world.logs}
    for referral in world.referrals:
        if referral.submitted_at is not None or referral.status.value == "ERROR":
            assert referral.id in logged
    assert {h.id for h in world.hub_referrals} <= logged


def test_logs_never_contain_patient_values(world: World) -> None:
    for patient in world.patients:
        values = [v for v in (patient.synthetic_nhi, patient.date_of_birth) if v]
        values += [f"{patient.given_name} {patient.family_name}"]
        for line in world.logs:
            assert all(v not in line.message for v in values), line


def test_f03_is_silent_in_events_but_warns_in_logs(world: World) -> None:
    cases = [
        rid for rid, code in world.labels.items() if code is FailureCode.F03_SOURCE_DOC_SKIPPED
    ]
    assert cases
    for referral_id in cases:
        rows = case_rows(world, referral_id)
        assert all(e.level is EventLevel.INFO for e in rows.referral_events)
        assert any(line.level is EventLevel.WARN for line in rows.logs)


def test_write_logs_produces_one_jsonl_file_per_service(world: World, tmp_path: Path) -> None:
    counts = write_logs(world, tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(f"{s}.jsonl" for s in SERVICES)
    assert sum(counts.values()) == len(world.logs)
    for service in SERVICES:
        lines = (tmp_path / f"{service}.jsonl").read_text().splitlines()
        assert len(lines) == counts[service]
        first = json.loads(lines[0])
        assert list(first) == list(LOG_FIELDS)
        assert first["service"] == service
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", first["timestamp"])
        timestamps = [json.loads(line)["timestamp"] for line in lines]
        assert timestamps == sorted(timestamps)


def test_log_files_are_byte_identical_across_runs(world: World, tmp_path: Path) -> None:
    write_logs(world, tmp_path / "a")
    write_logs(generate(CONFIG), tmp_path / "b")

    for service in SERVICES:
        a, b = ((tmp_path / d / f"{service}.jsonl").read_bytes() for d in ("a", "b"))
        assert hashlib.md5(a).hexdigest() == hashlib.md5(b).hexdigest()


def test_rewriting_replaces_old_files(world: World, tmp_path: Path) -> None:
    write_logs(world, tmp_path)
    write_logs(generate(SimConfig(seed=5, practices=2, clinics=3, referrals=5)), tmp_path)

    total = sum(len(p.read_text().splitlines()) for p in tmp_path.glob("*.jsonl"))
    assert total < 200
