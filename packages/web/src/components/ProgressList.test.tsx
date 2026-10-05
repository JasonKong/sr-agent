import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProgressList } from "./ProgressList";

describe("ProgressList", () => {
  it("names each check in plain words with the ids it looked at, not its settings", () => {
    render(
      <ProgressList
        busy={false}
        items={[
          {
            kind: "tool_call",
            call: {
              turn: 1,
              name: "search_logs",
              arguments: { referral_id: "GR-11854", limit: 200 },
              is_error: false,
              result_chars: 900,
              duration_ms: 20,
            },
          },
        ]}
      />,
    );
    const line = screen.getByText(/searching the logs/i);
    expect(line).toHaveTextContent("Searching the logs (GR-11854)");
    expect(line).not.toHaveTextContent("200");
  });

  it("is hidden until there is something to show", () => {
    const { container } = render(<ProgressList busy={false} items={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
