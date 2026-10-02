import { vi } from "vitest";
import type { DocumentDetail, OneTallyApi } from "../api";

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
  obligations: [{ kind: "return_by", dueDate: "2026-10-28", evidence: "RETURNS WITHIN 30 DAYS" }],
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

/** An OneTallyApi whose every method is a vi.fn() with an empty, successful default. */
export function fakeApi(overrides: Partial<OneTallyApi> = {}): OneTallyApi {
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
    saveSplit: vi.fn().mockResolvedValue({ version: 1, members: [], shares: [], totalCents: 0 }),
    ...overrides,
  };
}
