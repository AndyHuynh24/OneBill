import { describe, expect, it } from "vitest";
import type { DocumentLine } from "./api";
import { canSplit, parseNames } from "./split";

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
