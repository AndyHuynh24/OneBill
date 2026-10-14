import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AppUsage } from "./AppUsage";

const USAGE = {
  today: { scans: 12, pages: 4, emails: 3, renewals: 1 },
  month: { scans: 87, pages: 51, emails: 40, renewals: 5 },
  limits: { scans: 200, pages: 600, emails: 300 },
  days: [
    { date: "2026-10-12", scans: 12, pages: 4, emails: 3, renewals: 1 },
    { date: "2026-10-11", scans: 0, pages: 0, emails: 0, renewals: 0 },
    { date: "2026-10-10", scans: 7, pages: 9, emails: 2, renewals: 0 },
  ],
};

describe("AppUsage", () => {
  it("shows today and this month against the monthly limits", () => {
    render(<AppUsage usage={USAGE} />);

    const scans = screen.getByRole("row", { name: /^scans/i });
    expect(within(scans).getByText("12")).toBeInTheDocument();
    expect(scans).toHaveTextContent("87 / 200");
    expect(screen.getByRole("row", { name: /^pdf pages/i })).toHaveTextContent("51 / 600");
    expect(screen.getByRole("row", { name: /^emails read/i })).toHaveTextContent("40 / 300");
  });

  it("renewals have no limit to show", () => {
    render(<AppUsage usage={USAGE} />);

    const renewals = screen.getByRole("row", { name: /^renewals used/i });
    expect(renewals).toHaveTextContent(/^Renewals used15$/);
  });

  it("shows each of the last days, newest first", () => {
    render(<AppUsage usage={USAGE} />);

    expect(screen.getByRole("heading", { name: "Last 3 days" })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /^today 12 4 3 1$/i })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /^sun, oct 11 0 0 0 0$/i })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /^sat, oct 10 7 9 2 0$/i })).toBeInTheDocument();
  });

  it("shows the estimated AI cost, average per read, and tokens", () => {
    const ai = {
      today: { reads: 4, costMicroUsd: 61_600, inputTokens: 9_000, outputTokens: 2_100 },
      month: { reads: 120, costMicroUsd: 1_843_000, inputTokens: 260_000, outputTokens: 61_000 },
    };
    render(<AppUsage usage={{ ...USAGE, ai }} />);

    expect(screen.getByRole("heading", { name: "AI (estimated)" })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /^model reads 4 120$/i })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /^cost \$0\.0616 \$1\.84$/i })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /^avg per read \$0\.0154 \$0\.0154$/i })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /^tokens in \/ out 9,000 \/ 2,100 260,000 \/ 61,000$/i })).toBeInTheDocument();
  });

  it("shows no average before the first read", () => {
    const zero = { reads: 0, costMicroUsd: 0, inputTokens: 0, outputTokens: 0 };
    render(<AppUsage usage={{ ...USAGE, ai: { today: zero, month: zero } }} />);

    expect(screen.getByRole("row", { name: /^avg per read — —$/i })).toBeInTheDocument();
  });

  it("works without history or AI totals (an older API)", () => {
    render(<AppUsage usage={{ ...USAGE, days: undefined }} />);

    expect(screen.queryByRole("heading", { name: /last/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /ai/i })).not.toBeInTheDocument();
  });
});
