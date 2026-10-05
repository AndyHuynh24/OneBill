import type { Obligation, Subscription } from "./api";
import { DEADLINE_LABELS } from "./format";

/** One thing coming up: a subscription's next renewal, or a due date from a scanned/emailed bill. */
export interface UpcomingItem {
  key: string;
  name: string;
  /** "Renews", "Due", "Trial ends", "Payment due", ... */
  label: string;
  date: string;
  amountCents: number | null;
  currency: string;
  open: { subscription: string } | { document: string };
  warn?: boolean;
}

/** Reminders a subscription makes for itself: already shown as that subscription. */
const SUBSCRIPTION_REMINDERS = new Set(["renewal", "trial_ends"]);

/**
 * Subscriptions and bill due dates in one list, soonest first. A subscription's own renewal
 * reminder isn't listed twice; cancelled subscriptions and past dates are left out (a failed
 * payment stays, flagged, until it's sorted out).
 */
export function upcomingItems(subs: Subscription[], obligations: Obligation[], todayIso: string): UpcomingItem[] {
  const fromSubs: UpcomingItem[] = subs
    .filter((s) => s.status !== "cancelled" && s.nextRenewal && (s.nextRenewal >= todayIso || s.status === "payment_failed"))
    .map((s) => ({
      key: `sub-${s.serviceKey}`,
      name: s.service,
      label:
        s.status === "payment_failed"
          ? "Payment failed"
          : s.status === "trial_ending"
            ? "Trial ends"
            : s.category === "bill"
              ? "Due"
              : "Renews",
      date: s.nextRenewal!,
      amountCents: s.amountCents,
      currency: s.currency,
      open: { subscription: s.serviceKey },
      warn: s.status === "payment_failed",
    }));
  const fromBills: UpcomingItem[] = obligations
    .filter((o) => !SUBSCRIPTION_REMINDERS.has(o.kind) && o.dueDate >= todayIso)
    .map((o) => ({
      key: `obl-${o.oblId}`,
      name: o.vendor || "Bill",
      label: DEADLINE_LABELS[o.kind] ?? "Due",
      date: o.dueDate,
      amountCents: o.amountCents ?? null,
      currency: "USD",
      open: { document: o.docId },
    }));
  return [...fromSubs, ...fromBills].sort((a, b) => a.date.localeCompare(b.date) || a.name.localeCompare(b.name));
}

/** Due dates that came from bills (not subscriptions), for the full page's reminder list. */
export const billReminders = (obligations: Obligation[]) => obligations.filter((o) => !SUBSCRIPTION_REMINDERS.has(o.kind));
