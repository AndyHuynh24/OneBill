/** Split-bill helpers for the screens. Final shares are always computed by the server. */
import type { DocumentLine, Split } from "./api";
import { formatCents, formatDate } from "./format";

export const MAX_PEOPLE = 12;

export interface Person {
  name: string;
  self?: boolean;
}

/** Seat colors: warm and distinct, dark enough for white initials. */
export const SEAT_COLORS = [
  "#d9774f", // terracotta
  "#5f8f6b", // sage
  "#4a7ba6", // lake
  "#9b6aa8", // plum
  "#c08a2b", // ochre
  "#3f8f8a", // teal
  "#c25b7a", // rose
  "#6b7fae", // periwinkle
  "#a8643c", // cinnamon
  "#5b8f3f", // moss
  "#7a5fa8", // violet
  "#7d7268", // stone
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
// Spaces separate people ("Bao Linh Minh"); commas, "&", "+", and the word "and" do too.
const SEPARATORS = /[\s,;&+]+|\band\b/gi;

/** "Bao Linh Minh" → ["Bao", "Linh", "Minh"]. Use first names: "Linh Nguyen" is two people. */
export function parseNames(text: string, existing: string[] = []): string[] {
  const seen = new Set(existing.map((n) => n.toLocaleLowerCase()));
  const names: string[] = [];
  for (const part of (text ?? "").split(SEPARATORS)) {
    const name = part.slice(0, MAX_NAME).trim();
    if (name && !seen.has(name.toLocaleLowerCase())) {
      seen.add(name.toLocaleLowerCase());
      names.push(name);
    }
  }
  return names;
}

/**
 * Can this bill be split? Whenever it has items. A bill that doesn't add up (a misread
 * number) is still split, on the recorded items; the summary says it differs from the receipt.
 */
export function canSplit(lines: DocumentLine[] | undefined): boolean {
  return !!lines?.some((l) => l.kind === "item");
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

/** "Give to Bao" · "Split among Bao & Linh" · "Split among Bao, Linh +2" */
export function assignLabel(names: string[]): string {
  if (names.length === 1) return `Give to ${names[0]}`;
  if (names.length === 2) return `Split among ${names[0]} & ${names[1]}`;
  return `Split among ${names[0]}, ${names[1]} +${names.length - 2}`;
}
