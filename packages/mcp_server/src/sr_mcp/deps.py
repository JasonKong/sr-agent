"""What tools depend on: adapters and limits, injected so tests can use fakes."""

from __future__ import annotations

from dataclasses import dataclass

from sr_mcp.adapters.base import KnowledgeSearch, LogSource, ReferralStore


@dataclass(frozen=True)
class Deps:
    store: ReferralStore
    logs: LogSource
    knowledge: KnowledgeSearch
    max_result_chars: int = 8000
    caller: str = "stdio"  # recorded in the call log
