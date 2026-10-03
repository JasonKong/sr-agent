"""LogSource over the simulator's JSONL files (`var/logs/<service>.jsonl`).

Lines are loaded on first use and indexed by correlation id and referral id. Before each
search the files' sizes and modification times are checked, so a re-seed is picked up
without a restart. Lines that are not valid log records are skipped and counted.
"""

from __future__ import annotations

import threading
from pathlib import Path

from pydantic import ValidationError

from sr_mcp.adapters.base import LEVELS, LogQuery, LogRecord

_Signature = tuple[tuple[str, int, int], ...]


class JsonlLogSource:
    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._lock = threading.Lock()
        self._signature: _Signature | None = None
        self._records: list[LogRecord] = []
        self._by_correlation: dict[str, list[LogRecord]] = {}
        self._by_referral: dict[str, list[LogRecord]] = {}
        self.skipped_lines = 0

    def _current_signature(self) -> _Signature:
        if not self._directory.is_dir():
            return ()
        files = sorted(self._directory.glob("*.jsonl"))
        return tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)

    def _load_if_changed(self) -> None:
        signature = self._current_signature()
        if signature == self._signature:
            return
        records: list[LogRecord] = []
        skipped = 0
        for name, _, _ in signature:
            for line in (self._directory / name).read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    records.append(LogRecord.model_validate_json(line))
                except ValidationError:
                    skipped += 1
        by_correlation: dict[str, list[LogRecord]] = {}
        by_referral: dict[str, list[LogRecord]] = {}
        for record in records:
            by_correlation.setdefault(record.correlation_id, []).append(record)
            by_referral.setdefault(record.referral_id, []).append(record)
        self._records, self._by_correlation, self._by_referral = (
            records,
            by_correlation,
            by_referral,
        )
        self.skipped_lines, self._signature = skipped, signature

    def search(self, query: LogQuery) -> list[LogRecord]:
        with self._lock:
            self._load_if_changed()
            if query.correlation_id is not None:
                candidates = self._by_correlation.get(query.correlation_id, [])
            elif query.referral_id is not None:
                candidates = self._by_referral.get(query.referral_id, [])
            else:
                candidates = self._records
        floor = LEVELS.index(query.min_level) if query.min_level else 0
        found = [
            r
            for r in candidates
            if (query.referral_id is None or r.referral_id == query.referral_id)
            and query.service in (None, r.service)
            and LEVELS.index(r.level) >= floor
            and (query.since is None or r.timestamp >= query.since)
            and (query.until is None or r.timestamp <= query.until)
        ]
        found.sort(key=lambda r: (r.timestamp, r.id), reverse=True)
        return found[: query.limit]
