import { describe, expect, it } from "vitest";
import { centsToInput, daysUntil, formatCents, formatDate, parseDollars, parseSignedDollars, relativeDay, todayIso } from "./format";

describe("parseDollars", () => {
  it.each([
    ["26.81", 2681],
    ["$26.81", 2681],
    ["5", 500],
    ["5.5", 550],
    ["1,234.56", 123456],
    ["0.07", 7],
  ])("%s → %d cents", (input, cents) => {
    expect(parseDollars(input)).toBe(cents);
  });

  it.each(["", "abc", "1.234", "12.3.4", "-"])("rejects %j", (input) => {
    expect(parseDollars(input)).toBeNull();
  });

  it("avoids float rounding errors", () => {
    expect(parseDollars("0.29")).toBe(29); // 0.29 * 100 = 28.999999999999996 as a float
  });
});

describe("formatting", () => {
  it("formats cents as currency", () => {
    expect(formatCents(2618)).toBe("$26.18");
    expect(formatCents(-200)).toBe("-$2.00");
  });

  it("round-trips cents through an input value", () => {
    expect(centsToInput(2618)).toBe("26.18");
    expect(centsToInput(5)).toBe("0.05");
    expect(parseDollars(centsToInput(123456))).toBe(123456);
  });

  it("formats a calendar date without shifting a day", () => {
    expect(formatDate("2026-09-28")).toBe("Sep 28, 2026");
  });
});

describe("relative days", () => {
  const now = new Date(2026, 8, 30, 21, 30); // Sep 30, 9:30 pm local

  it("uses the local calendar day", () => {
    expect(todayIso(now)).toBe("2026-09-30");
    expect(daysUntil("2026-10-07", now)).toBe(7);
  });

  it.each([
    ["2026-09-30", "today"],
    ["2026-10-01", "tomorrow"],
    ["2026-10-03", "in 3 days"],
    ["2026-09-29", "yesterday"],
  ])("%s is %s", (date, label) => {
    expect(relativeDay(date, now)).toBe(label);
  });
});

describe("parseSignedDollars", () => {
  it.each([
    ["2.00", 200],
    ["-2.00", -200],
    ["- 1.5", -150],
    ["$1,234.56", 123456],
    ["-$3", -300],
  ])("%s → %d cents", (input, cents) => {
    expect(parseSignedDollars(input)).toBe(cents);
  });

  it.each(["", "-", "abc", "1.234", "--2"])("rejects %j", (input) => {
    expect(parseSignedDollars(input)).toBeNull();
  });
});
