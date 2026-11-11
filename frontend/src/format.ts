/** Display helpers. Money is integer cents; calendar dates are "YYYY-MM-DD" strings. */

// The decimal mark may be "," too: the iOS decimal keypad shows "," in comma-decimal regions.
// A comma before exactly 3 digits groups thousands; before 1–2 digits at the end it's the decimal.
const AMOUNT = /^\s*\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(?:[.,](\d{1,2}))?\s*$/;

/** "26.81" → 2681, "$1,234.5" → 123450, "26,81" → 2681. Parsed as text, never as a float. null if invalid. */
export function parseDollars(input: string): number | null {
  const match = AMOUNT.exec(input);
  if (!match) return null;
  const whole = Number(match[1].replace(/,/g, ""));
  const fraction = Number((match[2] ?? "").padEnd(2, "0"));
  return whole * 100 + fraction;
}

export function formatCents(cents: number, currency = "USD"): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(cents / 100);
}

/** Cents → "26.81" for an editable input. */
export function centsToInput(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

const MAX_TYPED_DIGITS = 9; // $9,999,999.99

/**
 * Bank-style entry: the field's digits are cents, filling from the right. "0.125" (after typing
 * a 5 into "0.12") → "1.25"; "12.5" (after a backspace on "12.50") → "1.25". No digits → "".
 */
export function typedAmount(raw: string, previous: string): string {
  const digits = raw.replace(/\D/g, "").replace(/^0+/, "");
  if (!digits) return "";
  if (digits.length > MAX_TYPED_DIGITS) return previous;
  return centsToInput(Number(digits));
}

/**
 * Parse "YYYY-MM-DD" as a *local* calendar date. (new Date("2026-09-28") is UTC midnight,
 * which shows as Sep 27 in the Americas.)
 */
function localDate(isoDate: string): Date {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function formatDate(isoDate: string): string {
  return localDate(isoDate).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function todayIso(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function daysUntil(isoDate: string, now: Date = new Date()): number {
  const today = localDate(todayIso(now));
  return Math.round((localDate(isoDate).getTime() - today.getTime()) / 86_400_000);
}

export function relativeDay(isoDate: string, now: Date = new Date()): string {
  const days = daysUntil(isoDate, now);
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  return days > 0 ? `in ${days} days` : `${-days} days ago`;
}

/** "5 min ago", "3 h ago", "3 days ago" for a past timestamp. */
export function timeAgo(iso: string, now: Date = new Date()): string {
  const minutes = Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

export const DEADLINE_LABELS: Record<string, string> = {
  payment_due: "Payment due",
  warranty_expires: "Warranty ends",
  renewal: "Renews",
  trial_ends: "Trial ends",
};

/** A bill's title: the store as read, "Reading…" until then, never the photo's file name. */
export function docTitle(doc: {
  vendor?: string | null;
  status?: string;
  contentType?: string;
}): string {
  if (doc.vendor) return doc.vendor;
  if (doc.status === "pending_upload" || doc.status === "processing") return "Reading…";
  return doc.contentType === "application/pdf" ? "Untitled bill" : "Untitled receipt";
}
