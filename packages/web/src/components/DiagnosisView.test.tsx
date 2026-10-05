import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DiagnosisView } from "./DiagnosisView";
import { diagnosisResponse } from "../test-data";

describe("DiagnosisView", () => {
  it("leads with the summary, then the root cause, confidence and next step", () => {
    render(<DiagnosisView result={diagnosisResponse()} />);

    const summary = screen.getByText(/PMS token expired/);
    const badge = screen.getByText("Clinic PMS authorisation failed");
    expect(summary.compareDocumentPosition(badge) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("F07_CLINIC_PMS_AUTH_FAILED")).toBeInTheDocument();
    expect(screen.getByText("High confidence")).toBeInTheDocument();
    expect(screen.getByText(/re-authorise its PMS connection/)).toBeInTheDocument();
    expect(screen.queryByText(/escalate to engineering/i)).not.toBeInTheDocument();
  });

  it("lists the evidence in an expandable section", async () => {
    render(<DiagnosisView result={diagnosisResponse()} />);

    const evidence = screen.getByRole("group", { name: /evidence \(2 records\)/i });
    expect(evidence).not.toHaveAttribute("open");
    await userEvent.click(within(evidence).getByText(/evidence \(2 records\)/i));
    expect(evidence).toHaveAttribute("open");
    expect(within(evidence).getByText("dl_538183")).toBeInTheDocument();
    expect(within(evidence).getByText("hub-worker:009502")).toBeInTheDocument();
    expect(within(evidence).getByText(/no attempts/)).toBeInTheDocument();
  });

  it("copies the draft reply", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    render(<DiagnosisView result={diagnosisResponse()} />);

    await user.click(screen.getByRole("button", { name: /copy draft reply/i }));
    expect(writeText).toHaveBeenCalledWith(
      "We have found the problem and will resend the referral today.",
    );
    expect(await screen.findByRole("button", { name: /copied/i })).toBeInTheDocument();
  });

  it("says plainly when the answer is incomplete or needs escalation", () => {
    const base = diagnosisResponse();
    const result = diagnosisResponse({
      outcome: "tool_limit",
      incomplete: true,
      diagnosis: base.diagnosis && {
        ...base.diagnosis,
        root_cause_code: null,
        confidence: "low",
        escalate_to_engineering: true,
      },
    });
    render(<DiagnosisView result={result} />);

    expect(screen.getByRole("alert")).toHaveTextContent(/incomplete.*limit of tool calls/i);
    expect(screen.getByText("Not enough evidence")).toBeInTheDocument();
    expect(screen.getByText(/escalate to engineering/i)).toBeInTheDocument();
  });

  it("shows a run without an answer", () => {
    render(
      <DiagnosisView
        result={diagnosisResponse({ outcome: "refused", incomplete: true, diagnosis: null })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/declined to answer/);
  });
});
