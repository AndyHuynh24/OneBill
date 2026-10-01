import { describe, expect, it } from "vitest";
import type { DocumentLine } from "./api";
import { addsUp, parseNames } from "./split";

describe("parseNames (mirrors the server)", () => {
  it.each([
    ["Linh, Minh and Bao", ["Linh", "Minh", "Bao"]],
    ["Tam & Vy + Khoa; An", ["Tam", "Vy", "Khoa", "An"]],
    ["Linh and Minh, and Bao", ["Linh", "Minh", "Bao"]],
    ["  Linh   Nguyen , bao ", ["Linh Nguyen", "bao"]],
    ["Andy and Sandy", ["Andy", "Sandy"]],
    ["Bao, bao", ["Bao"]],
    ["", []],
  ])("%j", (text, expected) => {
    expect(parseNames(text)).toEqual(expected);
  });

  it("skips people already in the split", () => {
    expect(parseNames("Bao, Tam", ["bao", "Me"])).toEqual(["Tam"]);
  });
});

const line = (row: number, kind: DocumentLine["kind"], amountCents: number): DocumentLine => ({
  row,
  description: kind,
  kind,
  amountCents,
});

describe("addsUp", () => {
  const lines = [line(1, "item", 1000), line(2, "discount", -100), line(3, "tax", 72), line(4, "total", 972)];

  it("is true when items, discounts, fees, tax and tip make the total (±2¢, like the server)", () => {
    expect(addsUp(lines, 972)).toBe(true);
    expect(addsUp(lines, 974)).toBe(true);
  });

  it("is false when they don't, or there's no total or no items", () => {
    expect(addsUp(lines, 1000)).toBe(false);
    expect(addsUp(lines, null)).toBe(false);
    expect(addsUp([line(1, "tax", 50)], 50)).toBe(false);
  });
});
