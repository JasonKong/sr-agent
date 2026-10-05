import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { HistoryList } from "./HistoryList";

const ITEMS = [
  {
    id: "dx_2",
    created_at: "2026-10-05T10:00:00Z",
    question: "Second question about GR-2",
    outcome: "answered",
    root_cause_code: "F06_MANUAL_ROUTING",
    confidence: "high",
    summary: "Routed manually.",
  },
  {
    id: "dx_1",
    created_at: "2026-10-05T09:00:00Z",
    question: "First question about GR-1",
    outcome: "tool_limit",
    root_cause_code: null,
    confidence: null,
    summary: null,
  },
];

describe("HistoryList", () => {
  it("lists past diagnoses with their root cause, and opens one", async () => {
    const onOpen = vi.fn();
    render(<HistoryList items={ITEMS} onOpen={onOpen} />);

    expect(screen.getByText(/Routed for manual delivery/)).toBeInTheDocument();
    expect(screen.getByText(/incomplete/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /first question about GR-1/i }));
    expect(onOpen).toHaveBeenCalledWith("dx_1");
  });

  it("says when there is nothing yet", () => {
    render(<HistoryList items={[]} onOpen={vi.fn()} />);
    expect(screen.getByText(/no diagnoses yet/i)).toBeInTheDocument();
  });
});
