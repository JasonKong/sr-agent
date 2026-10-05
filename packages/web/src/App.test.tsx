import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "./api/client";
import { App } from "./App";
import { diagnosisResponse } from "./test-data";

vi.mock("./api/client", async (original) => ({
  ...(await original<typeof client>()),
  diagnose: vi.fn(),
}));

describe("App", () => {
  beforeEach(() => {
    vi.mocked(client.diagnose).mockReset();
  });

  it("shows each check as it happens, then the answer", async () => {
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

    await userEvent.type(screen.getByLabelText(/what did the customer say/i), "Never arrived.");
    await userEvent.click(screen.getByRole("button", { name: /diagnose/i }));

    expect(await screen.findByText("Clinic PMS authorisation failed")).toBeInTheDocument();
    expect(screen.getByText(/looking up the referral in both systems/i)).toHaveTextContent(
      "GR-11854",
    );
    expect(vi.mocked(client.diagnose).mock.calls[0]?.[0]).toEqual({ question: "Never arrived." });
  });

  it("explains a failure in one line", async () => {
    vi.mocked(client.diagnose).mockRejectedValue(
      new client.ApiError(503, "cannot reach the MCP server"),
    );
    render(<App />);

    await userEvent.type(screen.getByLabelText(/what did the customer say/i), "Never arrived.");
    await userEvent.click(screen.getByRole("button", { name: /diagnose/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("cannot reach the MCP server");
    expect(screen.getByRole("button", { name: /diagnose/i })).toBeEnabled();
  });
});
