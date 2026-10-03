"""Streamable HTTP transport with a static bearer token (DESIGN.md section 7.2, ADR 0003).

The MCP app is stateless and answers with plain JSON (no server-sent events): every tool is
a single request and response, and a stateless service can later run on Lambda. A small
ASGI middleware in front of it checks `Authorization: Bearer <MCP_HTTP_TOKEN>` with a
constant-time comparison. `/healthz` is open and says only that the process is up.
"""

from __future__ import annotations

import hmac
import json

from mcp.server import MCPServer
from starlette.types import ASGIApp, Receive, Scope, Send

from sr_mcp.guard import log_event

MIN_TOKEN_LENGTH = 32
HEALTH_PATH = "/healthz"


async def _respond(
    send: Send, status: int, body: dict[str, str], extra: list[tuple[bytes, bytes]]
) -> None:
    payload = json.dumps(body).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers + extra})
    await send({"type": "http.response.body", "body": payload})


class BearerAuth:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token.encode()

    def _authorised(self, header: bytes) -> bool:
        scheme, _, presented = header.partition(b" ")
        # The scheme is case-insensitive (RFC 7235); the token is compared in constant time.
        return scheme.lower() == b"bearer" and hmac.compare_digest(presented, self._token)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan events go straight through
            await self._app(scope, receive, send)
            return
        if scope["path"] == HEALTH_PATH and scope["method"] == "GET":
            await _respond(send, 200, {"status": "ok"}, [])
            return
        presented = dict(scope["headers"]).get(b"authorization")
        if presented is None or not self._authorised(presented):
            log_event(
                "http_auth_failed",
                {"path": scope["path"], "reason": "missing" if presented is None else "invalid"},
            )
            challenge = [(b"www-authenticate", b'Bearer realm="sr-agent"')]
            await _respond(send, 401, {"error": "unauthorized"}, challenge)
            return
        await self._app(scope, receive, send)


def build_app(server: MCPServer, token: str, *, host: str) -> ASGIApp:
    if len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(f"MCP_HTTP_TOKEN must be at least {MIN_TOKEN_LENGTH} characters")
    app = server.streamable_http_app(stateless_http=True, json_response=True, host=host)
    return BearerAuth(app, token)
