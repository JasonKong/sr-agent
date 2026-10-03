"""The guard every tool runs through (DESIGN.md section 7.2).

`guarded_tool` registers a tool on the server and wraps each call to:

1. turn `NotFound` into a typed `NOT_FOUND` tool error, never an empty success;
2. scrub the result for anything that looks like a patient identifier;
3. cap the result size, dropping list items from the end and marking it truncated;
4. log the call to stderr: tool, caller, outcome, duration, size and redactions, but never
   the content. Stdout is reserved for the stdio protocol.
"""

from __future__ import annotations

import functools
import json
import sys
import time
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from sr_mcp.deps import Deps
from sr_mcp.masking import scrub

P = ParamSpec("P")


class ToolResult(BaseModel):
    """Base for every tool result."""

    truncated: bool = Field(
        default=False, description="True when items were left out to fit the size limit."
    )
    redacted: int = Field(
        default=0, description="How many values were replaced by [REDACTED] (patient data)."
    )


R = TypeVar("R", bound=ToolResult)


class NotFound(Exception):  # noqa: N818 - reads naturally at raise sites
    def __init__(self, kind: str, identifier: str) -> None:
        super().__init__(f"no {kind} with id {identifier}")


def _size(data: dict[str, Any]) -> int:
    return len(json.dumps(data, ensure_ascii=False))


def cap(data: dict[str, Any], max_chars: int) -> tuple[dict[str, Any], bool]:
    """Fit `data` into `max_chars` of JSON by trimming its largest list, then its longest text."""
    if _size(data) <= max_chars:
        return data, False
    data = dict(data)
    while _size(data) > max_chars:
        lists = [k for k, v in data.items() if isinstance(v, list) and v]
        if lists:
            key = max(lists, key=lambda k: _size({k: data[k]}))
            data[key] = data[key][:-1]
            continue
        texts = [k for k, v in data.items() if isinstance(v, str) and len(v) > 40]
        if not texts:
            break
        key = max(texts, key=lambda k: len(data[k]))
        data[key] = data[key][: len(data[key]) // 2] + "…"
    return data, True


def _log(record: dict[str, Any]) -> None:
    print(json.dumps({"event": "tool_call", **record}), file=sys.stderr, flush=True)


def guarded_tool(
    server: MCPServer, deps: Deps, **tool_options: Any
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(fn: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            started = time.perf_counter()
            record: dict[str, Any] = {"tool": fn.__name__, "caller": deps.caller}
            try:
                result = fn(*args, **kwargs)
                data, hits = scrub(result.model_dump(mode="json"))
                assert isinstance(data, dict)
                data, truncated = cap(data, deps.max_result_chars)
                data["truncated"] = bool(data.get("truncated")) or truncated
                data["redacted"] = int(data.get("redacted", 0)) + hits
                final = type(result).model_validate(data)
                record |= {"outcome": "ok", "result_chars": _size(data), "redacted": hits}
                return final
            except NotFound as error:
                record |= {"outcome": "not_found", "result_chars": 0, "redacted": 0}
                raise ToolError(f"NOT_FOUND: {error}") from error
            except Exception:
                record |= {"outcome": "error", "result_chars": 0, "redacted": 0}
                raise
            finally:
                record["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
                _log(record)

        server.tool(**tool_options)(wrapper)
        return wrapper

    return decorate
