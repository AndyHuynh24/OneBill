import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { UpcomingItem } from "../upcoming";
import { ComingUp } from "./ComingUp";

const NOW = new Date("2026-10-05T12:00:00");
const item = (name: string, date: string): UpcomingItem => ({
  key: name,
  name,
  label: "Renews",
  date,
  amountCents: 1000,
  currency: "USD",
  open: { subscription: name },
});

function shown(items: UpcomingItem[]) {
  render(
    <ComingUp
      items={items}
      subscriptions={[]}
      connection={{ connected: true, createdAt: null, lastUsedAt: null }}
      today="2026-10-05"
      now={NOW}
      onOpen={() => {}}
      onViewAll={() => {}}
      onAdd={() => {}}
      onConnect={() => {}}
    />,
  );
  return screen.getAllByRole("button", { name: /renews/i }).map((b) => b.textContent);
}

describe("ComingUp", () => {
  it("shows everything due in the next 30 days, however many", () => {
    const rows = shown(["A", "B", "C", "D", "E"].map((n, i) => item(n, `2026-10-${10 + i * 4}`)).concat(item("Later", "2026-12-01")));
    expect(rows).toHaveLength(5);
    expect(rows.join()).not.toContain("Later");
  });

  it("nothing later than 30 days, even when little is due soon", () => {
    const rows = shown([item("Soon", "2026-10-12"), item("Next month", "2026-11-20"), item("Winter", "2026-12-15")]);
    expect(rows).toEqual([expect.stringContaining("Soon")]);
  });
});
