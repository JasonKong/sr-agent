import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "./api/client";
import { App } from "./App";
import { diagnosisResponse } from "./test-data";

vi.mock("./api/client", async (original) => ({
  ...(await original<typeof client>()),
  diagnose: vi.fn(),
  getDiagnosis: vi.fn(),
  getHistory: vi.fn(),
  sendFeedback: vi.fn(),
}));

const PAST = {
  id: "dx_00000000000a",
  created_at: "2026-10-05T08:00:00Z",
  question: "An earlier question about GR-10080",
  outcome: "answered",
  root_cause_code: "F06_MANUAL_ROUTING",
  confidence: "high",
  summary: "Routed manually.",
};

async function ask(question: string) {
  await userEvent.type(screen.getByLabelText(/what did the customer say/i), question);
  await userEvent.click(screen.getByRole("button", { name: /^diagnose$/i }));
}

describe("App", () => {
  beforeEach(() => {
    vi.mocked(client.diagnose).mockReset();
    vi.mocked(client.getHistory).mockReset().mockResolvedValue([PAST]);
    vi.mocked(client.getDiagnosis).mockReset();
    vi.mocked(client.sendFeedback).mockReset().mockResolvedValue(undefined);
  });

  it("shows each check as it happens, then the answer, and refreshes the history", async () => {
    vi.mocked(client.diagnose).mockImplementation((_request, onProgress) => {
      onProgress({
        kind: "tool_call",
        call: {
          turn: 1,
          name: "get_referral",
          arguments: { referral_id: "GR-11854" },
          is_error: false,
          result_chars: 1800,
          duration_ms: 90,
        },
      });
      return Promise.resolve(diagnosisResponse());
    });
    render(<App />);
    expect(await screen.findByText(/an earlier question about GR-10080/i)).toBeInTheDocument();

    await ask("Never arrived.");

    expect(await screen.findByText("Clinic PMS authorisation failed")).toBeInTheDocument();
    expect(screen.getByText(/looking up the referral in both systems/i)).toHaveTextContent(
      "GR-11854",
    );
    expect(vi.mocked(client.diagnose).mock.calls[0]?.[0]).toEqual({ question: "Never arrived." });
    await waitFor(() => {
      expect(client.getHistory).toHaveBeenCalledTimes(2);
    });
  });

  it("opens a past diagnosis from the history", async () => {
    vi.mocked(client.getDiagnosis).mockResolvedValue(
      diagnosisResponse({ id: PAST.id, question: PAST.question }),
    );
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: /an earlier question/i }));

    expect(client.getDiagnosis).toHaveBeenCalledWith(PAST.id);
    expect(await screen.findByText("Clinic PMS authorisation failed")).toBeInTheDocument();
  });

  it("sends feedback on the answer shown", async () => {
    vi.mocked(client.diagnose).mockResolvedValue(diagnosisResponse());
    render(<App />);
    await ask("Never arrived.");

    await userEvent.click(await screen.findByRole("button", { name: /helpful/i }));
    expect(client.sendFeedback).toHaveBeenCalledWith("dx_0123456789ab", { rating: "UP" });
  });

  it("explains a failure in one line", async () => {
    vi.mocked(client.diagnose).mockRejectedValue(
      new client.ApiError(503, "cannot reach the MCP server"),
    );
    render(<App />);
    await ask("Never arrived.");

    expect(await screen.findByRole("alert")).toHaveTextContent("cannot reach the MCP server");
    expect(screen.getByRole("button", { name: /^diagnose$/i })).toBeEnabled();
  });
});
