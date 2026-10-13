import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AppUsage } from "./AppUsage";

const USAGE = {
  today: { scans: 12, pages: 4, emails: 3, renewals: 1 },
  month: { scans: 87, pages: 51, emails: 40, renewals: 5 },
  limits: { scans: 200, pages: 600, emails: 300 },
};

describe("AppUsage", () => {
  it("shows today and this month against the monthly limits", () => {
    render(<AppUsage usage={USAGE} />);

    const scans = screen.getByRole("row", { name: /scans/i });
    expect(within(scans).getByText("12")).toBeInTheDocument();
    expect(scans).toHaveTextContent("87 / 200");
    expect(screen.getByRole("row", { name: /pdf pages/i })).toHaveTextContent("51 / 600");
    expect(screen.getByRole("row", { name: /emails read/i })).toHaveTextContent("40 / 300");
  });

  it("renewals have no limit to show", () => {
    render(<AppUsage usage={USAGE} />);

    const renewals = screen.getByRole("row", { name: /renewals used/i });
    expect(renewals).toHaveTextContent(/^Renewals used15$/);
  });
});
