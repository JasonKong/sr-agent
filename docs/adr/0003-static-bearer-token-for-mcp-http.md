# 0003. A static bearer token for the MCP server's HTTP transport

- Status: Accepted
- Date: 2026-10-04
- Deciders: @Ming

## Context

The project agent (M3), and later remote clients, reach the MCP server over Streamable HTTP. Anything served over HTTP needs authentication, even on a laptop: another process, or a page in a browser, could otherwise call the tools.

The MCP Python SDK supports OAuth 2.1 resource servers. A token verifier, an authorization server, and protected-resource metadata for discovery would all have to be set up. DESIGN.md already scopes v1 to a bearer token, with OAuth through Cognito as an open question for later.

## Decision

- **A shared secret.** `sr-mcp --transport http` requires `MCP_HTTP_TOKEN`, at least 32 characters, and refuses to start without it. The secret comes from `.env` locally and from Secrets Manager in AWS.
- **A small ASGI middleware in front of the SDK's app checks `Authorization: Bearer <token>`.**
  - The scheme is case-insensitive (RFC 7235).
  - The token is compared in constant time (`hmac.compare_digest`).
  - Failures get `401` with `WWW-Authenticate: Bearer`, and are logged with the path and the reason (`missing` or `invalid`), never the token.
- **`/healthz` is open** and returns only `{"status": "ok"}`, for load balancers and process managers.
- **The app is stateless with JSON responses** (`stateless_http=True`, `json_response=True`). Every tool is a single request and response, so no session state or event stream is needed, and the service can run on Lambda later.
- **It binds to `127.0.0.1` by default.** `--host` changes this, for example in a container.

## Alternatives considered

| Option | Why not chosen |
| --- | --- |
| The SDK's OAuth resource-server support | It needs an authorization server and discovery metadata before the agent can make its first call. It is the right step once there are real users (Cognito), not for one agent and one server |
| No authentication on localhost | Local services are still reachable by other local processes and by browsers (DNS rebinding). The habit matters more than the threat model of a laptop |
| Mutual TLS | Strong, but heavy to set up locally, and a load balancer in AWS would terminate TLS anyway |

## Consequences

- **Easier:** the agent and tests authenticate with one header; the middleware is about 30 lines and fully tested, including mutation checks.
- **Harder:** one shared secret means no per-user identity, audit by caller or revocation of a single client. Rotation means changing the secret everywhere.
- **Revisit when:** more than one client needs its own identity, or CS users reach the server through the web console. Then move to OAuth with Cognito (DESIGN.md section 11).
