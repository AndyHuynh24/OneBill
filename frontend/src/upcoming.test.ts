import { describe, expect, it } from "vitest";
import type { Obligation, Subscription } from "./api";
import { billReminders, upcomingItems } from "./upcoming";

const sub = (over: Partial<Subscription>): Subscription => ({
  serviceKey: "netflix",
  service: "Netflix",
  plan: null,
  amountCents: 1549,
  currency: "USD",
  cycle: "monthly",
  status: "charged",
  nextRenewal: "2026-10-20",
  renewalComputed: false,
  history: [],
  issues: [],
  ...over,
});
const obl = (over: Partial<Obligation>): Obligation => ({ oblId: "o", docId: "d", kind: "payment_due", dueDate: "2026-10-10", ...over });
const TODAY = "2026-10-05";

describe("upcomingItems", () => {
  it("merges subscriptions and bill due dates, soonest first", () => {
    const items = upcomingItems(
      [sub({}), sub({ serviceKey: "rent", service: "Rent", category: "bill", nextRenewal: "2026-11-01" })],
      [obl({ vendor: "PG&E", dueDate: "2026-10-10", amountCents: 8734 })],
      TODAY,
    );
    expect(items.map((i) => [i.name, i.label, i.date])).toEqual([
      ["PG&E", "Payment due", "2026-10-10"],
      ["Netflix", "Renews", "2026-10-20"],
      ["Rent", "Due", "2026-11-01"],
    ]);
    expect(items[0].open).toEqual({ document: "d" });
    expect(items[1].open).toEqual({ subscription: "netflix" });
  });

  it("doesn't list a subscription twice (its own renewal reminder is left out)", () => {
    const items = upcomingItems([sub({})], [obl({ kind: "renewal", vendor: "Netflix", dueDate: "2026-10-20" })], TODAY);
    expect(items).toHaveLength(1);
  });

  it("leaves out cancelled subscriptions, past dates, and ones without a date", () => {
    const items = upcomingItems(
      [sub({ status: "cancelled" }), sub({ serviceKey: "old", nextRenewal: "2026-10-01" }), sub({ serviceKey: "x", nextRenewal: null })],
      [obl({ dueDate: "2026-10-01" })],
      TODAY,
    );
    expect(items).toEqual([]);
  });

  it("keeps a failed payment, flagged, even after its date", () => {
    const [item] = upcomingItems([sub({ status: "payment_failed", nextRenewal: "2026-10-01" })], [], TODAY);
    expect(item.label).toBe("Payment failed");
    expect(item.warn).toBe(true);
  });

  it("says when a trial ends", () => {
    const [item] = upcomingItems([sub({ status: "trial_ending" })], [], TODAY);
    expect(item.label).toBe("Trial ends");
  });
});

describe("billReminders", () => {
  it("keeps due dates from bills, not subscription reminders", () => {
    const kept = billReminders([obl({ oblId: "a" }), obl({ oblId: "b", kind: "renewal" }), obl({ oblId: "c", kind: "trial_ends" })]);
    expect(kept.map((o) => o.oblId)).toEqual(["a"]);
  });
});

describe("a bill added by hand", () => {
  const rent = sub({ serviceKey: "rent", service: "Rent", category: "bill", amountCents: 210000, nextRenewal: "2026-11-01", status: "upcoming" });
  // The reminder the bill makes for itself: it carries the bill's key.
  const ownReminder = obl({ oblId: "o1", docId: "", kind: "payment_due", dueDate: "2026-11-01", vendor: "Rent", subKey: "rent" });

  it("is listed once, as the bill you can open", () => {
    const items = upcomingItems([rent], [ownReminder], TODAY);

    expect(items.map((i) => [i.name, i.label])).toEqual([["Rent", "Due"]]);
    expect(items[0].open).toEqual({ subscription: "rent" });
  });

  it("isn't counted again among the bill due dates", () => {
    expect(billReminders([ownReminder])).toEqual([]);
  });

  it("a due date from a scanned bill is still listed", () => {
    const scanned = obl({ oblId: "o2", docId: "d1", dueDate: "2026-10-20", vendor: "PG&E" });

    expect(upcomingItems([], [scanned], TODAY).map((i) => i.open)).toEqual([{ document: "d1" }]);
  });
});
