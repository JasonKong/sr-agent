"""Structured JSONL logs, one file per service (DESIGN.md section 3.3).

Logs are generated with the rows (`generate.py`) and written here as one JSON object per
line, keys in a fixed order and timestamps in UTC, so the files are byte-identical for the
same config. Locally the MCP server reads these files; in AWS the same lines go to CloudWatch.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sr_simulator.rows import LogLine, World

SERVICES = ("composer-web", "composer-worker", "hub-api", "hub-worker")
LOG_FIELDS = (
    "id",
    "timestamp",
    "level",
    "service",
    "referral_id",
    "correlation_id",
    "leg",
    "event",
    "error_code",
    "message",
)


def format_timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def to_json(line: LogLine) -> str:
    record = {
        "id": line.id,
        "timestamp": format_timestamp(line.timestamp),
        "level": line.level.value,
        "service": line.service,
        "referral_id": line.referral_id,
        "correlation_id": line.correlation_id,
        "leg": line.leg,
        "event": line.event,
        "error_code": line.error_code,
        "message": line.message,
    }
    return json.dumps(record, ensure_ascii=False)


def write_logs(world: World, directory: Path) -> dict[str, int]:
    """Write (replacing) one `<service>.jsonl` file per service; return lines per service."""
    directory.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for service in SERVICES:
        lines = sorted((line for line in world.logs if line.service == service), key=lambda x: x.id)
        text = "".join(to_json(line) + "\n" for line in lines)
        (directory / f"{service}.jsonl").write_text(text)
        counts[service] = len(lines)
    return counts
