/** Subscription helpers for the screens. Dates are "YYYY-MM-DD" calendar days. */
import type { BillingCycle, Subscription } from "./api";

const SUFFIX: Record<string, string> = { weekly: "/wk", monthly: "/mo", quarterly: "/qtr", yearly: "/yr" };
const MONTHS: Record<string, number> = { monthly: 1, quarterly: 3, yearly: 12 };

export const cycleSuffix = (cycle: BillingCycle | null) => (cycle ? (SUFFIX[cycle] ?? "") : "");

type Ymd = [number, number, number];
const parse = (iso: string): Ymd => iso.split("-").map(Number) as Ymd;
const key = ([y, m, d]: Ymd) => y * 10000 + m * 100 + d;
const daysIn = (y: number, m: number) => new Date(y, m, 0).getDate();

/** `months` after the anchor date, keeping the anchor's day (Jan 31 → Feb 28 → Mar 31). */
function addMonths([y, m, d]: Ymd, months: number): Ymd {
  const total = m - 1 + months;
  const year = y + Math.floor(total / 12);
  const month = (total % 12) + 1;
  return [year, month, Math.min(d, daysIn(year, month))];
}

function addDays([y, m, d]: Ymd, days: number): Ymd {
  const date = new Date(y, m - 1, d + days);
  return [date.getFullYear(), date.getMonth() + 1, date.getDate()];
}

/**
 * What active subscriptions will cost next calendar month: every renewal that falls in it
 * (weekly plans 4–5 times, yearly ones only in their month). Cancelled plans and ones without
 * a known price or renewal date are left out.
 */
export function nextMonthTotal(subs: Subscription[], todayIso: string): { totalCents: number; count: number } {
  const [ty, tm] = parse(todayIso);
  const first = addMonths([ty, tm, 1], 1);
  const last: Ymd = [first[0], first[1], daysIn(first[0], first[1])];
  let totalCents = 0;
  let count = 0;
  for (const s of subs) {
    if (s.status === "cancelled" || s.amountCents == null || !s.nextRenewal) continue;
    const anchor = parse(s.nextRenewal);
    let renewals = 0;
    for (let i = 0; i < 60; i++) {
      const at =
        s.cycle === "weekly" ? addDays(anchor, 7 * i) : s.cycle && MONTHS[s.cycle] ? addMonths(anchor, MONTHS[s.cycle] * i) : anchor;
      if (key(at) > key(last)) break;
      if (key(at) >= key(first)) renewals += 1;
      if (!(s.cycle === "weekly" || (s.cycle && MONTHS[s.cycle]))) break; // unknown cycle: one date only
    }
    if (renewals) {
      totalCents += s.amountCents * renewals;
      count += 1;
    }
  }
  return { totalCents, count };
}
