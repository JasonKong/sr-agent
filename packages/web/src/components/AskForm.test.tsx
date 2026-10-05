import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AskForm } from "./AskForm";

describe("AskForm", () => {
  it("sends the question and an optional referral id", async () => {
    const onSubmit = vi.fn();
    render(<AskForm busy={false} onSubmit={onSubmit} />);

    await userEvent.type(screen.getByLabelText(/what did the customer say/i), "It never arrived.");
    await userEvent.type(screen.getByLabelText(/referral id/i), "gr-11854");
    await userEvent.click(screen.getByRole("button", { name: /diagnose/i }));

    expect(onSubmit).toHaveBeenCalledWith({
      question: "It never arrived.",
      referral_id: "GR-11854",
    });
  });

  it("leaves the referral id out when it is empty", async () => {
    const onSubmit = vi.fn();
    render(<AskForm busy={false} onSubmit={onSubmit} />);
    await userEvent.type(screen.getByLabelText(/what did the customer say/i), "Did it arrive?");
    await userEvent.click(screen.getByRole("button", { name: /diagnose/i }));
    expect(onSubmit).toHaveBeenCalledWith({ question: "Did it arrive?" });
  });

  it("refuses a malformed referral id and a too-short question", async () => {
    const onSubmit = vi.fn();
    render(<AskForm busy={false} onSubmit={onSubmit} />);

    expect(screen.getByRole("button", { name: /diagnose/i })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/what did the customer say/i), "Where is it now?");
    await userEvent.type(screen.getByLabelText(/referral id/i), "10234");
    expect(screen.getByText(/looks like GR-10234 or HR-55210/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /diagnose/i })).toBeDisabled();
  });

  it("is disabled while a diagnosis is running", () => {
    render(<AskForm busy onSubmit={vi.fn()} />);
    expect(screen.getByRole("button", { name: /diagnosing/i })).toBeDisabled();
  });
});
