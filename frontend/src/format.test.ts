import { describe, expect, it } from "vitest";
import { centsToInput, daysUntil, formatCents, formatDate, parseDollars, relativeDay, timeAgo, todayIso, typedAmount } from "./format";

describe("parseDollars", () => {
  it.each([
    ["26.81", 2681],
    ["$26.81", 2681],
    ["5", 500],
    ["5.5", 550],
    ["1,234.56", 123456],
    ["0.07", 7],
    // iOS decimal keypad shows "," in comma-decimal regions
    ["26,81", 2681],
    ["5,5", 550],
    ["1,234", 123400],
    ["1,234,56", 123456],
  ])("%s → %d cents", (input, cents) => {
    expect(parseDollars(input)).toBe(cents);
  });

  it.each(["", "abc", "1.234", "12.3.4", "-", "12,", "1,2345", "1,234.5,6"])("rejects %j", (input) => {
    expect(parseDollars(input)).toBeNull();
  });

  it("avoids float rounding errors", () => {
    expect(parseDollars("0.29")).toBe(29); // 0.29 * 100 = 28.999999999999996 as a float
  });
});

describe("typedAmount (digits fill from the right, like a bank app)", () => {
  it.each([
    ["1", "", "0.01"],
    ["0.012", "0.01", "0.12"],
    ["0.125", "0.12", "1.25"],
    ["1.250", "1.25", "12.50"],
    ["12.5", "12.50", "1.25"], // backspace drops the last digit
    ["0.0", "0.01", ""], // deleting the last digit empties the field
    ["0", "", ""],
    ["12a", "", "0.12"], // non-digits are ignored
    ["", "12.50", ""],
  ])("%j (was %j) → %j", (raw, previous, expected) => {
    expect(typedAmount(raw, previous)).toBe(expected);
  });

  it("ignores a digit past $9,999,999.99", () => {
    expect(typedAmount("9999999.999", "9999999.99")).toBe("9999999.99");
  });

  it("round-trips through parseDollars", () => {
    expect(parseDollars(typedAmount("1.250", "1.25"))).toBe(1250);
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

describe("timeAgo", () => {
  const now = new Date("2026-10-01T19:00:00Z");
  it.each([
    ["2026-10-01T18:59:40Z", "just now"],
    ["2026-10-01T18:55:00Z", "5 min ago"],
    ["2026-10-01T16:00:00Z", "3 h ago"],
    ["2026-09-28T19:00:00Z", "3 days ago"],
  ])("%s → %s", (iso, text) => {
    expect(timeAgo(iso, now)).toBe(text);
  });
});
