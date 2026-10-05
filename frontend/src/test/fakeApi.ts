import { vi } from "vitest";
import type { DocumentDetail, OneBillApi, Subscription } from "../api";

export const RECEIPT: DocumentDetail = {
  docId: "1790000000000aaaaaaaa",
  status: "needs_review",
  filename: "receipt.jpg",
  vendor: "TARGT",
  documentDate: "2026-09-28",
  totalCents: 2618,
  currency: "USD",
  createdAt: "2026-09-28T10:00:00+00:00",
  issues: ["The total was hard to read (OCR confidence 62%). Please check it."],
  lines: [
    { row: 1, description: "BANANAS", kind: "item", amountCents: 129 },
    { row: 2, description: "COUPON", kind: "discount", amountCents: -200 },
    { row: 3, description: "SUBTOTAL", kind: "subtotal", amountCents: 2448 },
    { row: 4, description: "TAX", kind: "tax", amountCents: 170 },
    { row: 5, description: "TOTAL", kind: "total", amountCents: 2618 },
    { row: 6, description: "CASH", kind: "payment", amountCents: 3000 },
    { row: 7, description: "CHANGE", kind: "change", amountCents: 382 },
  ],
  obligations: [{ kind: "payment_due", dueDate: "2026-10-28", evidence: "PAY BY 10/28/2026" }],
};

/** A dinner whose lines add up (can be split). */
export const DINNER: DocumentDetail = {
  docId: "1790000000000bbbbbbbb",
  status: "ready",
  vendor: "THAI BASIL",
  documentDate: "2026-10-01",
  totalCents: 2886,
  currency: "USD",
  createdAt: "2026-10-01T19:00:00+00:00",
  updatedAt: "2026-10-01T19:05:00+00:00",
  issues: [],
  lines: [
    { row: 1, description: "PAD THAI", kind: "item", amountCents: 1450 },
    { row: 2, description: "CURRY", kind: "item", amountCents: 1200 },
    { row: 3, description: "SUBTOTAL", kind: "subtotal", amountCents: 2650 },
    { row: 4, description: "TAX", kind: "tax", amountCents: 236 },
    { row: 5, description: "TOTAL", kind: "total", amountCents: 2886 },
  ],
  obligations: [],
};

export const NETFLIX: Subscription = {
  serviceKey: "netflix",
  service: "Netflix",
  plan: "Standard",
  amountCents: 1549,
  currency: "USD",
  cycle: "monthly",
  status: "charged",
  nextRenewal: "2026-11-01",
  renewalComputed: false,
  lastChargedAt: "2026-10-01",
  history: [{ date: "2026-10-01", amountCents: 1549, status: "charged" }],
  issues: [],
};

/** An OneBillApi whose every method is a vi.fn() with an empty, successful default. */
export function fakeApi(overrides: Partial<OneBillApi> = {}): OneBillApi {
  return {
    getProfile: vi.fn().mockResolvedValue({ userId: "u", baseCurrency: "USD", createdAt: "x" }),
    listDocuments: vi.fn().mockResolvedValue({
      documents: [],
      review: { reviewed: 0, acceptedUnchanged: 0, acceptanceRate: null },
    }),
    getDocument: vi.fn().mockResolvedValue(RECEIPT),
    reviewDocument: vi.fn().mockResolvedValue({ ...RECEIPT, status: "ready" }),
    listObligations: vi.fn().mockResolvedValue({ obligations: [] }),
    uploadDocument: vi.fn().mockResolvedValue("new-doc"),
    baseUrl: "https://api.example.com/",
    listSubscriptions: vi.fn().mockResolvedValue({
      subscriptions: [],
      connection: { connected: false, createdAt: null, lastUsedAt: null },
    }),
    cancelSubscription: vi.fn().mockResolvedValue({ ...NETFLIX, status: "cancelled", nextRenewal: null }),
    editSubscription: vi.fn().mockResolvedValue(NETFLIX),
    createIngestKey: vi.fn().mockResolvedValue({ key: "obk_" + "k".repeat(43) }),
    revokeIngestKey: vi.fn().mockResolvedValue(undefined),
    deleteSubscription: vi.fn().mockResolvedValue(undefined),
    addSubscription: vi.fn().mockResolvedValue({ ...NETFLIX, source: "manual" }),
    setSubscriptionReminders: vi.fn().mockResolvedValue({ ...NETFLIX, remindersOff: true }),
    deleteObligation: vi.fn().mockResolvedValue(undefined),
    deleteDocument: vi.fn().mockResolvedValue(undefined),
    saveSplit: vi.fn().mockResolvedValue({ version: 1, members: [], shares: [], totalCents: 0 }),
    ...overrides,
  };
}
