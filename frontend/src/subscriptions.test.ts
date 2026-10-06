import { describe, expect, it } from "vitest";
import type { Subscription } from "./api";
import { activeCount, cycleLabel, cycleSuffix, monthlyCost, nextMonthTotal, renewingWithin } from "./subscriptions";

const sub = (over: Partial<Subscription>): Subscription => ({
  serviceKey: "x",
  service: "X",
  plan: null,
  amountCents: 1000,
  currency: "USD",
  cycle: "monthly",
  status: "charged",
  nextRenewal: "2026-10-15",
  renewalComputed: false,
  history: [],
  issues: [],
  ...over,
});

// "Today" is Oct 1, 2026, so "next month" is November.
const TODAY = "2026-10-01";

describe("nextMonthTotal", () => {
  it("counts a monthly plan once, even if its next renewal is still this month", () => {
    expect(nextMonthTotal([sub({ nextRenewal: "2026-10-15" })], TODAY)).toEqual({ totalCents: 1000, count: 1 });
    expect(nextMonthTotal([sub({ nextRenewal: "2026-11-30" })], TODAY).totalCents).toBe(1000);
  });

  it("keeps the renewal day across short months (Jan 31 → Feb 28 → Mar 31)", () => {
    expect(nextMonthTotal([sub({ nextRenewal: "2027-01-31" })], "2027-01-05").totalCents).toBe(1000); // Feb 28
    expect(nextMonthTotal([sub({ nextRenewal: "2027-01-31" })], "2027-02-05").totalCents).toBe(1000); // Mar 31
  });

  it("counts a weekly plan every week of next month", () => {
    // Nov 2026 has Mondays on the 2nd, 9th, 16th, 23rd, 30th.
    expect(nextMonthTotal([sub({ cycle: "weekly", nextRenewal: "2026-10-05" })], TODAY).totalCents).toBe(5000);
  });

  it("counts a yearly plan only in its renewal month", () => {
    expect(nextMonthTotal([sub({ cycle: "yearly", nextRenewal: "2026-11-20" })], TODAY).totalCents).toBe(1000);
    expect(nextMonthTotal([sub({ cycle: "yearly", nextRenewal: "2027-03-20" })], TODAY).totalCents).toBe(0);
  });

  it("counts a quarterly plan when a quarter lands next month", () => {
    expect(nextMonthTotal([sub({ cycle: "quarterly", nextRenewal: "2026-08-10" })], TODAY).totalCents).toBe(1000);
    expect(nextMonthTotal([sub({ cycle: "quarterly", nextRenewal: "2026-09-10" })], TODAY).totalCents).toBe(0);
  });

  it("skips cancelled plans and ones without a price or renewal date", () => {
    const subs = [
      sub({ status: "cancelled" }),
      sub({ amountCents: null }),
      sub({ nextRenewal: null }),
      sub({ amountCents: 1199 }),
    ];

    expect(nextMonthTotal(subs, TODAY)).toEqual({ totalCents: 1199, count: 1 });
  });

  it("counts an unknown cycle only on its known renewal date", () => {
    expect(nextMonthTotal([sub({ cycle: null, nextRenewal: "2026-11-03" })], TODAY).totalCents).toBe(1000);
    expect(nextMonthTotal([sub({ cycle: "other", nextRenewal: "2026-10-03" })], TODAY).totalCents).toBe(0);
  });

  it("works across the year boundary", () => {
    expect(nextMonthTotal([sub({ nextRenewal: "2026-12-15" })], "2026-12-01").totalCents).toBe(1000); // January
  });
});

describe("cycleSuffix", () => {
  it.each([
    ["monthly", "/mo"],
    ["yearly", "/yr"],
    ["weekly", "/wk"],
    ["quarterly", "/qtr"],
    [null, ""],
  ])("%s → %j", (cycle, suffix) => {
    expect(cycleSuffix(cycle as Subscription["cycle"])).toBe(suffix);
  });
});

describe("renewingWithin (next 30 days)", () => {
  it("adds every renewal from today through the next 30 days", () => {
    const subs = [
      sub({ amountCents: 1549, nextRenewal: "2026-10-01" }), // today counts
      sub({ amountCents: 1199, nextRenewal: "2026-10-31" }), // day 30 counts
      sub({ amountCents: 999, nextRenewal: "2026-11-01" }), // day 31 doesn't
      sub({ amountCents: 500, cycle: "weekly", nextRenewal: "2026-10-02" }), // Oct 2, 9, 16, 23, 30
      sub({ amountCents: 100, status: "cancelled" }),
    ];

    expect(renewingWithin(subs, TODAY, 30)).toEqual({ totalCents: 1549 + 1199 + 5 * 500, count: 3 });
  });
});

describe("activeCount and cycleLabel", () => {
  it("counts what you're still paying for", () => {
    expect(activeCount([sub({}), sub({ status: "cancelled" }), sub({ status: "trial_ending" })])).toBe(2);
  });

  it.each([
    ["monthly", "Monthly"],
    ["yearly", "Yearly"],
    ["weekly", "Weekly"],
    ["quarterly", "Every 3 months"],
    [null, "—"],
  ])("%s → %s", (cycle, label) => {
    expect(cycleLabel(cycle as Subscription["cycle"])).toBe(label);
  });
});

describe("monthlyCost", () => {
  const base = { serviceKey: "s", service: "S", plan: null, currency: "USD", status: "charged", nextRenewal: null, renewalComputed: false, history: [], issues: [] } as const;
  const sub = (over: Partial<Subscription>): Subscription => ({ ...base, history: [], issues: [], amountCents: null, cycle: null, ...over });
  it("turns every active subscription into a monthly amount", () => {
    expect(
      monthlyCost([
        sub({ amountCents: 1500, cycle: "monthly" }),
        sub({ amountCents: 12000, cycle: "yearly" }), // $10/month
        sub({ amountCents: 1200, cycle: "weekly" }), // $52/month
        sub({ amountCents: 3000, cycle: "quarterly" }), // $10/month
        sub({ amountCents: 999, cycle: "monthly", status: "cancelled" }), // left out
        sub({ amountCents: null, cycle: "monthly" }), // no amount: left out
      ]),
    ).toBe(1500 + 1000 + 5200 + 1000);
  });
});
