# M6 — CS console: learning note

> Draft by Claude, from the M6 work. The demo video is yours to record (`TODO(me)` below).

Milestone M6 · Tasks T6.1–T6.2 · October 2026

## What I built

| Task | Result |
| --- | --- |
| T6.1 Console | React, Vite and Tailwind in strict TypeScript; ask and answer screens; API types generated from the agent's OpenAPI schema |
| T6.2 Feedback and tests | Thumbs up or down with the right root cause; history; Playwright end-to-end tests, including accessibility |

## What I learned

### A generated contract between Python and TypeScript

- **Types are generated, never written.** `pnpm api:types` turns `sra openapi` into TypeScript, so a change to a Pydantic model in the agent breaks the console's build, not a CS member's screen. That is the boundary ADR 0001 promised: components meet only at a protocol with a schema.
- **Strict settings catch real mistakes.**
  - `exactOptionalPropertyTypes` and `noUncheckedIndexedAccess` caught several "maybe undefined" cases.
  - ESLint's type-aware rules caught a fallback that the types claimed could never run, which was really an untyped assumption about unknown codes.

### The browser never holds a secret

The dev server proxies `/api` to the agent and adds its bearer token from the repository's `.env`, on the server side. The page cannot leak what it never had. In AWS, the same job moves to Cognito and the API gateway (section 9).

### Show the work, in the user's words

- **Progress is one plain-English line per tool call**, with the ids it looked at ("Checking the deliveries to the clinic (GR-11854)"), streamed as it happens. Twenty seconds of silence reads as broken; twenty seconds of visible checking reads as thorough.
- **Only the ids, never the settings.** An argument like `limit=200` means nothing to CS; the first version showed it, and the visual check caught it.
- **The answer leads with the summary,** then the root cause in plain words with its code, then the next step. The evidence is one click away, not in the way.

### End-to-end tests that know the answer

- **Against the real services.** Playwright starts the real MCP server, the real agent API and the console.
- **With the oracle in place of the model,** the tests are deterministic and free, and they still exercise every service the user's request touches.
- **The same tests can run with the real model** (`E2E_API=live`).
- **Seeing the test fail matters as much as seeing it pass.** Breaking one label made the main test fail, as it should.
- **Accessibility is checked, not assumed.** axe-core runs on the ask and answer screens, on desktop and phone widths.

## What I measured

| Measure | Value |
| --- | --- |
| Unit tests (Vitest) | 26 |
| End-to-end tests (Playwright) | 3, each on desktop and phone width (6 runs, about 8 s with the oracle) |
| Accessibility (axe-core, WCAG 2 A and AA) | No violations on the ask or answer screen |
| Production bundle | 228 KB of JavaScript (72 KB gzipped) |

## The demo

`TODO(me)`: record a two-to-three-minute video of a CS member resolving three cases, as DESIGN.md section 10 asks. GR-11854 (F07), GR-10008 (F11) and GR-10080 (F06) work well. With an API key, use `make api`; without one, `uv run sr-evals serve-oracle`, and say so in the video.

## Interview talking points

- "The console's types are generated from the agent's OpenAPI schema, so a backend change breaks the frontend build, not the user."
- "The end-to-end tests run the real services with an oracle in place of the model: deterministic, free, and still a test of everything else the user's request touches."
- "The browser never holds the API token: the dev server's proxy adds it."
