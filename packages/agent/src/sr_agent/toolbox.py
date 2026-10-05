"""Tools the model can call, from the MCP server (DESIGN.md section 7.4).

The agent is an MCP client and nothing else: it has no database or log access. Tool errors
(an unknown id, an argument that fails validation) come back from the server as results
with `is_error`, and go to the model as such, so it can correct itself.

Anything raised inside an MCP `Client` context comes out wrapped in nested exception
groups. `open_toolbox` unwraps them: transport failures become `ToolsUnavailable` with a
reason a person can act on, and the caller's own exceptions come out unchanged.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

import httpx2
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel


class ToolsUnavailable(Exception):  # noqa: N818 - reads naturally at raise sites
    """The MCP server could not be reached or refused the client."""


class ToolOutcome(BaseModel):
    text: str
    is_error: bool = False


class Toolbox(Protocol):
    @property
    def instructions(self) -> str:
        """What the server says about itself, for the system prompt."""
        ...

    def specs(self) -> list[dict[str, Any]]:
        """Tool definitions for the model: name, description and input schema."""
        ...

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome: ...


class McpToolbox:
    def __init__(self, client: Client, tools: list[types.Tool]) -> None:
        self._client = client
        self._tools = tools

    @classmethod
    async def load(cls, client: Client) -> McpToolbox:
        tools: list[types.Tool] = []
        cursor: str | None = None
        while True:
            page = await client.list_tools(cursor=cursor)
            tools += page.tools
            cursor = page.next_cursor
            if not cursor:
                return cls(client, tools)

    @property
    def instructions(self) -> str:
        return self._client.instructions or ""

    def specs(self) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in self._tools
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        result = await self._client.call_tool(name, arguments)
        if result.structured_content is not None and not result.is_error:
            # The same data as the text content, without the indentation: fewer tokens.
            text = json.dumps(result.structured_content, ensure_ascii=False, separators=(",", ":"))
        else:
            text = "\n".join(
                c.text if isinstance(c, types.TextContent) else f"[{c.type} content omitted]"
                for c in result.content
            )
        return ToolOutcome(text=text, is_error=result.is_error)


class _UnauthorisedError(Exception):
    pass


async def _refuse_401(response: httpx2.Response) -> None:
    if response.status_code == 401:
        raise _UnauthorisedError


def _leaves(error: BaseException) -> Iterator[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        for inner in error.exceptions:
            yield from _leaves(inner)
    else:
        yield error


def _diagnose(leaves: list[BaseException], url: str) -> ToolsUnavailable | None:
    if any(isinstance(e, _UnauthorisedError) for e in leaves):
        return ToolsUnavailable(
            f"the MCP server at {url} refused the token (401): check MCP_HTTP_TOKEN"
        )
    if any(isinstance(e, httpx2.ConnectError) for e in leaves):
        return ToolsUnavailable(
            f"cannot reach the MCP server at {url}: is it running? Start it with `make mcp-http`"
        )
    transport = next((e for e in leaves if isinstance(e, httpx2.TransportError)), None)
    if transport is not None:
        return ToolsUnavailable(f"the MCP server at {url} failed: {transport!r}")
    return None


@asynccontextmanager
async def open_toolbox(client: Client, *, url: str = "in-process") -> AsyncIterator[McpToolbox]:
    """Connect `client` and yield its tools. Works for any MCP transport."""
    try:
        async with client:
            yield await McpToolbox.load(client)
    except BaseExceptionGroup as group:
        leaves = list(_leaves(group))
        diagnosis = _diagnose(leaves, url)
        if diagnosis is not None:
            raise diagnosis from group
        own = [e for e in leaves if not isinstance(e, _UnauthorisedError)]
        if len(own) == 1:
            raise own[0] from None
        raise


@asynccontextmanager
async def connect_http(
    url: str, token: str, *, timeout_s: float = 30.0
) -> AsyncIterator[McpToolbox]:
    """The MCP server over Streamable HTTP, with its bearer token (ADR 0003)."""
    http = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        event_hooks={"response": [_refuse_401]},
        timeout=timeout_s,
    )
    async with (
        http,
        open_toolbox(Client(streamable_http_client(url, http_client=http)), url=url) as toolbox,
    ):
        yield toolbox


class _Without:
    def __init__(self, inner: Toolbox, hidden: set[str]) -> None:
        self._inner = inner
        self._hidden = hidden

    @property
    def instructions(self) -> str:
        return self._inner.instructions

    def specs(self) -> list[dict[str, Any]]:
        return [s for s in self._inner.specs() if s["name"] not in self._hidden]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        if name in self._hidden:
            return ToolOutcome(text=f"Tool {name} is not available.", is_error=True)
        return await self._inner.call(name, arguments)


def without(toolbox: Toolbox, hidden: set[str]) -> Toolbox:
    """The same tools minus `hidden`, for ablations such as an eval run without RAG."""
    return _Without(toolbox, hidden)


# One-line descriptions, for the tool-description ablation (T5.1): how much do the server's
# detailed descriptions (when to use a tool, what it shows and does not show) help?
MINIMAL_DESCRIPTIONS: dict[str, str] = {
    "get_referral": "A referral in both systems, by Composer or Hub id.",
    "find_referrals": "Find referrals by clinic, practice, status or dates.",
    "get_referral_timeline": "The events of a referral in both systems, in time order.",
    "get_deliveries": "The Hub's deliveries of a referral and their attempts.",
    "get_recipient": "A clinic: its account, specialists and PMS integrations.",
    "get_return_path": "The specialist's responses and their filing back to the GP.",
    "search_logs": "Log lines for a referral or correlation id.",
    "list_failed_referrals": "Referrals with a recorded failure in a recent window.",
    "search_knowledge": "Search past tickets and runbooks.",
    "get_runbook": "The runbook for a root-cause code.",
}


class _Described:
    def __init__(self, inner: Toolbox, descriptions: dict[str, str]) -> None:
        self._inner = inner
        self._descriptions = descriptions

    @property
    def instructions(self) -> str:
        return self._inner.instructions

    def specs(self) -> list[dict[str, Any]]:
        return [
            s | {"description": self._descriptions.get(s["name"], s["description"])}
            for s in self._inner.specs()
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        return await self._inner.call(name, arguments)


def with_descriptions(toolbox: Toolbox, descriptions: dict[str, str]) -> Toolbox:
    """The same tools with other descriptions; schemas and behaviour are unchanged."""
    return _Described(toolbox, descriptions)
