/** Split-bill helpers for the screens. Final shares are always computed by the server. */
import type { DocumentLine } from "./api";

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
