import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FeedbackPanel } from "./FeedbackPanel";

describe("FeedbackPanel", () => {
  it("sends a thumbs up at once", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    render(<FeedbackPanel diagnosisId="dx_1" send={send} />);

    await userEvent.click(screen.getByRole("button", { name: /helpful/i }));

    expect(send).toHaveBeenCalledWith("dx_1", { rating: "UP" });
    expect(await screen.findByText(/thanks/i)).toBeInTheDocument();
  });

  it("asks for the right root cause after a thumbs down", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    render(<FeedbackPanel diagnosisId="dx_1" send={send} />);

    await userEvent.click(screen.getByRole("button", { name: /not right/i }));
    await userEvent.selectOptions(
      screen.getByLabelText(/what was the right root cause/i),
      "F08_CLINIC_PMS_TIMEOUT",
    );
    await userEvent.type(screen.getByLabelText(/comment/i), "It was a timeout.");
    await userEvent.click(screen.getByRole("button", { name: /send feedback/i }));

    expect(send).toHaveBeenCalledWith("dx_1", {
      rating: "DOWN",
      correct_code: "F08_CLINIC_PMS_TIMEOUT",
      comment: "It was a timeout.",
    });
    expect(await screen.findByText(/thanks/i)).toBeInTheDocument();
  });

  it("offers every root cause in plain words", async () => {
    render(<FeedbackPanel diagnosisId="dx_1" send={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /not right/i }));
    const options = screen.getAllByRole("option");
    expect(options.map((o) => o.textContent)).toContain("Clinic PMS timing out");
    expect(options).toHaveLength(13); // a prompt and the 12 codes
  });

  it("says when feedback could not be sent", async () => {
    const send = vi.fn().mockRejectedValue(new Error("the API answered 500"));
    render(<FeedbackPanel diagnosisId="dx_1" send={send} />);
    await userEvent.click(screen.getByRole("button", { name: /helpful/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not send");
  });
});
