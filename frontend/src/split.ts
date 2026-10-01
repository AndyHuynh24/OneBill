/** Split-bill helpers for the screens. Final shares are always computed by the server. */
import type { DocumentLine, Split } from "./api";
import { formatCents, formatDate } from "./format";

export const MAX_PEOPLE = 12;

export interface Person {
  name: string;
  self?: boolean;
}

/** Seat colors: distinct, dark enough for white initials. */
export const SEAT_COLORS = [
  "#2563eb",
  "#16a34a",
  "#ea580c",
  "#9333ea",
  "#db2777",
  "#0891b2",
  "#ca8a04",
  "#4f46e5",
  "#dc2626",
  "#059669",
  "#7c3aed",
  "#64748b",
];

export const seatColor = (seat: number) => SEAT_COLORS[seat % SEAT_COLORS.length];

export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return words
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
}

const MAX_NAME = 40;
const SEPARATORS = /[,;\n&+]|\band\b/i;

/** "Linh, Minh and Bao" → ["Linh", "Minh", "Bao"]. Mirrors services/split/names.py. */
export function parseNames(text: string, existing: string[] = []): string[] {
  const seen = new Set(existing.map((n) => n.toLocaleLowerCase()));
  const names: string[] = [];
  for (const part of (text ?? "").split(new RegExp(SEPARATORS, "gi"))) {
    const name = part.split(/\s+/).filter(Boolean).join(" ").slice(0, MAX_NAME).trim();
    if (name && !seen.has(name.toLocaleLowerCase())) {
      seen.add(name.toLocaleLowerCase());
      names.push(name);
    }
  }
  return names;
}

const SPLIT_KINDS = new Set(["item", "discount", "fee", "tax", "tip"]);
const TOLERANCE_CENTS = 2; // matches the server

/** Can this bill be split? Items/discounts/fees/tax/tip must make the total (±2¢). */
export function addsUp(lines: DocumentLine[] | undefined, totalCents: number | null | undefined): boolean {
  if (totalCents == null || !lines?.some((l) => l.kind === "item")) return false;
  const sum = lines.filter((l) => SPLIT_KINDS.has(l.kind)).reduce((acc, l) => acc + l.amountCents, 0);
  return Math.abs(sum - totalCents) <= TOLERANCE_CENTS;
}

/** The message for the share sheet: "Bao $15.79 (PAD THAI + tax/tip/fees)". */
export function shareText(
  split: Split,
  lines: DocumentLine[],
  meta: { vendor?: string | null; documentDate?: string | null; currency?: string | null },
): string {
  const currency = meta.currency ?? "USD";
  const description = new Map(lines.map((l) => [l.row, l.description]));
  const header = [meta.vendor, meta.documentDate && formatDate(meta.documentDate), formatCents(split.totalCents, currency)]
    .filter(Boolean)
    .join(" · ");
  const rows = split.shares.map((share) => {
    const member = split.members.find((m) => m.id === share.member);
    const items = share.rows.map((r) => description.get(r)).filter(Boolean).join(", ");
    const extras = share.extrasCents ? " + tax/tip/fees" : "";
    const detail = items || extras ? ` (${items || "—"}${extras})` : "";
    return `${member?.name ?? "?"} ${formatCents(share.totalCents, currency)}${detail}`;
  });
  return [header, ...rows].join("\n");
}
