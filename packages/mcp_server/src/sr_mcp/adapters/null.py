"""Placeholder adapters for sources not built yet (logs in T2.4, knowledge in T4.2)."""

from __future__ import annotations

from sr_mcp.adapters.base import KnowledgeHit, LogQuery, LogRecord


class NoLogs:
    def search(self, query: LogQuery) -> list[LogRecord]:
        return []


class NoKnowledge:
    def search(self, query: str, *, error_code: str | None, top_k: int) -> list[KnowledgeHit]:
        return []
