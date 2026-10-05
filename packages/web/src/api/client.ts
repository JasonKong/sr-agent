/**
 * A small client for the agent's HTTP API (DESIGN.md section 7.4). Types come from its
 * OpenAPI schema (`pnpm api:types`), so a change to the API breaks the build here.
 *
 * Requests go to `/api`, which the dev server proxies to the API with its bearer token.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];
export type DiagnosisRequest = Schemas["DiagnosisRequest"];
export type DiagnosisResponse = Schemas["DiagnosisResponse"];
export type Diagnosis = Schemas["Diagnosis"];
export type Evidence = Schemas["Evidence"];
export type FailureCode = Schemas["FailureCode"];
export type Feedback = Schemas["Feedback"];
export type HistoryItem = Schemas["HistoryItem"];
export type ToolCall = Schemas["ToolCallRecord"];

export type Progress =
  | { kind: "model_call"; turn: number; stopReason: string | null }
  | { kind: "tool_call"; call: ToolCall };

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const BASE = "/api";

async function failure(response: Response): Promise<ApiError> {
  let detail = `the API answered ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    // not JSON: keep the status
  }
  return new ApiError(response.status, detail);
}

export interface ServerEvent {
  event: string;
  data: unknown;
}

/** Complete server-sent events in `buffer`, and the unfinished text after them. */
export function parseEvents(buffer: string): { events: ServerEvent[]; rest: string } {
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events = blocks
    .filter((block) => block.trim() !== "")
    .map((block) => {
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      return { event, data: JSON.parse(data) as unknown };
    });
  return { events, rest };
}

/** Run a diagnosis, reporting each model turn and tool call as it happens. */
export async function diagnose(
  request: DiagnosisRequest,
  onProgress: (progress: Progress) => void,
  fetchFn: typeof fetch = fetch,
): Promise<DiagnosisResponse> {
  const response = await fetchFn(`${BASE}/diagnoses`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(request),
  });
  if (!response.ok || response.body === null) throw await failure(response);

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const parsed = parseEvents(buffer + value);
    buffer = parsed.rest;
    for (const { event, data } of parsed.events) {
      if (event === "model_call") {
        const d = data as { turn: number; stop_reason: string | null };
        onProgress({ kind: "model_call", turn: d.turn, stopReason: d.stop_reason });
      } else if (event === "tool_call") {
        onProgress({ kind: "tool_call", call: data as ToolCall });
      } else if (event === "done") {
        return data as DiagnosisResponse;
      } else if (event === "error") {
        throw new ApiError(503, (data as { detail: string }).detail);
      }
    }
  }
  throw new ApiError(502, "the diagnosis stream ended without a result");
}

export async function getDiagnosis(
  id: string,
  fetchFn: typeof fetch = fetch,
): Promise<DiagnosisResponse> {
  const response = await fetchFn(`${BASE}/diagnoses/${encodeURIComponent(id)}`);
  if (!response.ok) throw await failure(response);
  return (await response.json()) as DiagnosisResponse;
}

export async function getHistory(
  limit = 20,
  fetchFn: typeof fetch = fetch,
): Promise<HistoryItem[]> {
  const response = await fetchFn(`${BASE}/diagnoses?limit=${limit}`);
  if (!response.ok) throw await failure(response);
  return (await response.json()) as HistoryItem[];
}

export async function sendFeedback(
  id: string,
  feedback: Feedback,
  fetchFn: typeof fetch = fetch,
): Promise<void> {
  const response = await fetchFn(`${BASE}/diagnoses/${encodeURIComponent(id)}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(feedback),
  });
  if (!response.ok) throw await failure(response);
}
