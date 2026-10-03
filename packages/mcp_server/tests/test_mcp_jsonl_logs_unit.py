"""Unit tests for the JSONL LogSource: indexing, filters, ordering, reloads, bad lines."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sr_mcp.adapters.base import LogQuery
from sr_mcp.adapters.jsonl_logs import JsonlLogSource

T0 = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)


def _line(n: int, service: str, level: str, referral_id: str, corr: str) -> str:
    return json.dumps(
        {
            "id": f"{service}:{n:06d}",
            "timestamp": (T0 + timedelta(seconds=n)).isoformat().replace("+00:00", "Z"),
            "level": level,
            "service": service,
            "referral_id": referral_id,
            "correlation_id": corr,
            "leg": "deliver",
            "event": "EVENT",
            "error_code": None if level == "INFO" else "CODE",
            "message": f"message {n}",
        }
    )


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    (tmp_path / "composer-worker.jsonl").write_text(
        "\n".join(_line(n, "composer-worker", "INFO", "GR-1", "corr-a") for n in range(1, 4)) + "\n"
    )
    (tmp_path / "hub-worker.jsonl").write_text(
        _line(4, "hub-worker", "INFO", "HR-9", "corr-a")
        + "\n"
        + _line(5, "hub-worker", "ERROR", "HR-9", "corr-a")
        + "\n"
        + _line(6, "hub-worker", "WARN", "HR-8", "corr-b")
        + "\n"
        + "{not json\n"
    )
    return tmp_path


def test_finds_both_systems_by_correlation_newest_first(log_dir: Path) -> None:
    lines = JsonlLogSource(log_dir).search(LogQuery(correlation_id="corr-a"))

    assert [line.id for line in lines] == [
        "hub-worker:000005",
        "hub-worker:000004",
        "composer-worker:000003",
        "composer-worker:000002",
        "composer-worker:000001",
    ]


def test_finds_by_native_referral_id(log_dir: Path) -> None:
    lines = JsonlLogSource(log_dir).search(LogQuery(referral_id="HR-9"))
    assert {line.referral_id for line in lines} == {"HR-9"}
    assert len(lines) == 2


def test_filters_by_service_level_and_time(log_dir: Path) -> None:
    source = JsonlLogSource(log_dir)

    assert len(source.search(LogQuery(correlation_id="corr-a", service="hub-worker"))) == 2
    warn_up = source.search(LogQuery(min_level="WARN"))
    assert {line.level for line in warn_up} == {"WARN", "ERROR"}
    window = source.search(
        LogQuery(
            correlation_id="corr-a",
            since=T0 + timedelta(seconds=2),
            until=T0 + timedelta(seconds=4),
        )
    )
    assert [line.id for line in window] == [
        "hub-worker:000004",
        "composer-worker:000003",
        "composer-worker:000002",
    ]


def test_limit(log_dir: Path) -> None:
    assert len(JsonlLogSource(log_dir).search(LogQuery(correlation_id="corr-a", limit=2))) == 2


def test_bad_lines_are_skipped_and_counted(log_dir: Path) -> None:
    source = JsonlLogSource(log_dir)
    source.search(LogQuery(correlation_id="corr-a"))
    assert source.skipped_lines == 1


def test_reloads_when_files_change(log_dir: Path) -> None:
    source = JsonlLogSource(log_dir)
    assert source.search(LogQuery(correlation_id="corr-new")) == []

    path = log_dir / "hub-api.jsonl"
    path.write_text(_line(9, "hub-api", "INFO", "HR-7", "corr-new") + "\n")
    os.utime(path, None)

    assert [line.id for line in source.search(LogQuery(correlation_id="corr-new"))] == [
        "hub-api:000009"
    ]


def test_a_missing_directory_has_no_logs(tmp_path: Path) -> None:
    assert JsonlLogSource(tmp_path / "nope").search(LogQuery(correlation_id="x")) == []
