"""Unit tests for `sim case`: picking a labelled case and resolving its evidence. No database."""

import json
from pathlib import Path

import pytest

from sr_db.domain import FailureCode
from sr_simulator.cli import main, write_labels
from sr_simulator.config import SimConfig
from sr_simulator.generate import generate
from sr_simulator.rows import World

CONFIG = SimConfig(seed=5, practices=6, clinics=12, referrals=400, failure_rate=0.2)


@pytest.fixture(scope="module")
def world() -> World:
    return generate(CONFIG)


@pytest.fixture
def labels(world: World, tmp_path: Path) -> Path:
    path = tmp_path / "labels.json"
    write_labels(path, CONFIG, world.labels)
    return path


def _case(capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, object]:
    assert main(["case", *args]) == 0
    result = json.loads(capsys.readouterr().out)
    assert isinstance(result, dict)
    return result


def test_labels_file_records_the_full_config(labels: Path) -> None:
    document = json.loads(labels.read_text())

    assert document["config"] == {
        "seed": 5,
        "anchor_time": CONFIG.anchor_time.isoformat(),
        "practices": 6,
        "clinics": 12,
        "referrals": 400,
        "history_days": 90,
        "failure_rate": 0.2,
    }


def test_case_resolves_evidence_to_real_record_ids(
    world: World, labels: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = _case(capsys, "F07_CLINIC_PMS_AUTH_FAILED", "--labels", str(labels))

    assert world.labels[str(case["composer_id"])] is FailureCode.F07_CLINIC_PMS_AUTH_FAILED
    hub = next(h for h in world.hub_referrals if h.id == case["hub_id"])
    assert hub.source_ref == case["composer_id"]
    assert case["root_cause"]
    evidence = case["evidence"]
    assert isinstance(evidence, list)
    known = {row.id for _, rows in world.tables() for row in rows}  # type: ignore[attr-defined]
    known |= {line.id for line in world.logs}
    ids = [item["record_id"] for item in evidence]
    assert ids
    assert set(ids) <= known
    assert any(i.startswith("hub-worker:") for i in ids)
    assert {item["table"] for item in evidence} >= {"hub.clinic_integration", "hub.delivery", "log"}


def test_picks_are_stable_and_distinct(labels: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = _case(capsys, "F04_HUB_UNAVAILABLE", "--pick", "0", "--labels", str(labels))
    again = _case(capsys, "F04_HUB_UNAVAILABLE", "--pick", "0", "--labels", str(labels))
    second = _case(capsys, "F04_HUB_UNAVAILABLE", "--pick", "1", "--labels", str(labels))

    assert first == again
    assert first["composer_id"] != second["composer_id"]
    assert first["hub_id"] is None


def test_f00_picks_an_unlabelled_referral(
    world: World, labels: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = _case(capsys, "F00_NO_FAULT", "--labels", str(labels))

    assert case["composer_id"] not in world.labels


def test_picking_past_the_available_cases_fails_clearly(
    labels: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["case", "F06_MANUAL_ROUTING", "--pick", "999", "--labels", str(labels)]) == 1
    assert "only" in capsys.readouterr().err


def test_a_missing_labels_file_says_to_seed_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.json"

    assert main(["case", "F01_MISSING_REQUIRED_FIELD", "--labels", str(missing)]) == 1
    assert "sim seed" in capsys.readouterr().err
