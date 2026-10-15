import { describe, expect, it } from "vitest";
import type { DocumentLine } from "./api";
import { DINNER } from "./test/fakeApi";
import { canSplit, evenSplit, parseNames } from "./split";

describe("parseNames", () => {
  it.each([
    ["Bao Linh Minh", ["Bao", "Linh", "Minh"]], // spaces separate people
    ["  Bao   Linh ", ["Bao", "Linh"]],
    ["Linh, Minh and Bao", ["Linh", "Minh", "Bao"]], // commas and "and" work too (dictation)
    ["Tam & Vy + Khoa", ["Tam", "Vy", "Khoa"]],
    ["Andy Sandy", ["Andy", "Sandy"]], // "and" only counts as a whole word
    ["Bao bao BAO", ["Bao"]],
    ["", []],
  ])("%j", (text, expected) => {
    expect(parseNames(text)).toEqual(expected);
  });

  it("skips people already in the split", () => {
    expect(parseNames("Bao Tam", ["bao", "Me"])).toEqual(["Tam"]);
  });
});

const line = (row: number, kind: DocumentLine["kind"], amountCents: number): DocumentLine => ({
  row,
  description: kind,
  kind,
  amountCents,
});

describe("canSplit", () => {
  it("is true whenever the bill has items, even if its numbers don't add up", () => {
    expect(canSplit([line(1, "item", 1000), line(2, "total", 5000)])).toBe(true);
    expect(canSplit([line(1, "tax", 50)])).toBe(false);
    expect(canSplit(undefined)).toBe(false);
  });
});

describe("evenSplit", () => {
  it("shares every item among everyone and spreads tax, tips and fees by what each person had", () => {
    const doc = {
      ...DINNER,
      updatedAt: "2026-10-03T19:00:00Z",
      lines: [
        { row: 1, description: "PAD THAI", kind: "item" as const, amountCents: 1450 },
        { row: 2, description: "COUPON", kind: "discount" as const, amountCents: -200 },
        { row: 3, description: "TAX", kind: "tax" as const, amountCents: 120 },
        { row: 4, description: "TIP", kind: "tip" as const, amountCents: 300 },
        { row: 5, description: "TOTAL", kind: "total" as const, amountCents: 1670 },
      ],
    };

    const request = evenSplit(doc, [{ name: "Me", self: true }, { name: "Minh" }], 1);

    expect(request).toEqual({
      members: [{ name: "Me", self: true }, { name: "Minh" }],
      assignments: { "1": [0, 1], "2": [0, 1] },
      charges: { "3": { mode: "proportional" }, "4": { mode: "proportional" } },
      expectedVersion: 0,
      expectedUpdatedAt: "2026-10-03T19:00:00Z",
      paidBy: 1,
    });
  });

  it("leaves tax alone when prices already include it", () => {
    const doc = {
      ...DINNER,
      taxIncluded: true,
      lines: [
        { row: 1, description: "PHO", kind: "item" as const, amountCents: 1500 },
        { row: 2, description: "VAT", kind: "tax" as const, amountCents: 150 },
      ],
    };

    expect(evenSplit(doc, [{ name: "Me", self: true }, { name: "Minh" }]).charges).toEqual({});
  });
});
