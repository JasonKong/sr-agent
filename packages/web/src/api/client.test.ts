import { describe, expect, it, vi } from "vitest";

import { ApiError, diagnose, getHistory, parseEvents, sendFeedback, type Progress } from "./client";
import { diagnosisResponse } from "../test-data";

function sse(...events: [string, unknown][]): string {
  return events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join("");
}

function streaming(body: string, chunk = 17): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (let i = 0; i < body.length; i += chunk) {
        controller.enqueue(encoder.encode(body.slice(i, i + chunk)));
      }
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("parseEvents", () => {
  it("splits complete events and keeps the unfinished rest", () => {
    const { events, rest } = parseEvents('event: a\ndata: {"x":1}\n\nevent: b\ndata: {"y"');
    expect(events).toEqual([{ event: "a", data: { x: 1 } }]);
    expect(rest).toBe('event: b\ndata: {"y"');
  });
});

describe("diagnose", () => {
  it("reports progress as it streams and returns the result", async () => {
    const body = sse(
      ["started", { question: "q" }],
      ["model_call", { turn: 1, stop_reason: "tool_use", duration_ms: 900 }],
      ["tool_call", { name: "get_referral", arguments: { referral_id: "GR-1" }, is_error: false }],
      ["done", diagnosisResponse()],
    );
    const fetchFn = vi.fn<typeof fetch>().mockResolvedValue(streaming(body));
    const seen: Progress[] = [];

    const result = await diagnose({ question: "Why?" }, (p) => seen.push(p), fetchFn);

    expect(result.id).toBe("dx_0123456789ab");
    expect(seen.map((p) => p.kind)).toEqual(["model_call", "tool_call"]);
    const [url, init] = fetchFn.mock.calls[0] ?? [];
    expect(url).toBe("/api/diagnoses");
    expect(new Headers(init?.headers).get("Accept")).toBe("text/event-stream");
  });

  it("turns an error event or status into an ApiError with the reason", async () => {
    const failing = vi
      .fn<typeof fetch>()
      .mockResolvedValue(streaming(sse(["error", { detail: "cannot reach the MCP server" }])));
    await expect(diagnose({ question: "Why?" }, () => undefined, failing)).rejects.toThrow(
      "cannot reach the MCP server",
    );

    const busy = vi
      .fn<typeof fetch>()
      .mockResolvedValue(Response.json({ detail: "busy" }, { status: 429 }));
    await expect(diagnose({ question: "Why?" }, () => undefined, busy)).rejects.toMatchObject({
      status: 429,
      message: "busy",
    });
  });
});

describe("history and feedback", () => {
  it("call their endpoints", async () => {
    const fetchFn = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json([]))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    expect(await getHistory(5, fetchFn)).toEqual([]);
    await sendFeedback("dx_1", { rating: "UP" }, fetchFn);

    expect(fetchFn.mock.calls.map((c) => c[0])).toEqual([
      "/api/diagnoses?limit=5",
      "/api/diagnoses/dx_1/feedback",
    ]);
  });

  it("raises on a failed request", async () => {
    const fetchFn = vi.fn<typeof fetch>().mockResolvedValue(new Response("", { status: 500 }));
    await expect(getHistory(5, fetchFn)).rejects.toBeInstanceOf(ApiError);
  });
});
