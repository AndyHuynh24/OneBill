/** Split-bill helpers for the screens. Final shares are always computed by the server. */
import type { DocumentDetail, DocumentLine, Split, SplitRequest } from "./api";
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

/**
 * The message for the share sheet: a header, then each person with one line per item
 * ("- PAD THAI $12.00", "- SPRING ROLLS $6.00 (split 3 ways)") and their tax/tip/fees.
 * People already marked as paid are left out; `only` limits it to one person (a reminder).
 */
export function shareText(
  split: Split,
  lines: DocumentLine[],
  meta: { vendor?: string | null; documentDate?: string | null; currency?: string | null },
  only?: string,
): string {
  const currency = meta.currency ?? "USD";
  const byRow = new Map(lines.map((l) => [l.row, l]));
  const header = [meta.vendor, meta.documentDate && formatDate(meta.documentDate), formatCents(split.totalCents, currency)]
    .filter(Boolean)
    .join(" · ");
  const shares = split.shares.filter((s) => (only ? s.member === only : !split.paid?.[s.member]));
  const people = shares.map((share) => {
    const member = split.members.find((m) => m.id === share.member);
    const items = share.rows.flatMap((row) => {
      const line = byRow.get(row);
      if (!line) return [];
      const ways = split.assignments[String(row)]?.length ?? 1;
      const shared = ways > 1 ? ` (split ${ways} ways)` : "";
      return [`- ${line.description} ${formatCents(line.amountCents, currency)}${shared}`];
    });
    const extras = share.extrasCents ? [`- Tax/tip/fees ${formatCents(share.extrasCents, currency)}`] : [];
    return [`${member?.name ?? "?"}: ${formatCents(share.totalCents, currency)}`, ...items, ...extras].join("\n");
  });
  return [header, ...people].join("\n\n");
}

/** "Give to Bao" · "Split among Bao & Linh" · "Split among Bao, Linh +2" */
export function assignLabel(names: string[]): string {
  if (names.length === 1) return `Give to ${names[0]}`;
  if (names.length === 2) return `Split among ${names[0]} & ${names[1]}`;
  return `Split among ${names[0]}, ${names[1]} +${names.length - 2}`;
}

/**
 * "Split evenly" for a trip receipt nobody has split yet: every item and discount shared by
 * everyone, tax/tip/fees by what each person had (tax is skipped when prices include it).
 * The server still computes every share.
 */
export function evenSplit(doc: DocumentDetail, people: Person[], paidBy?: number): SplitRequest {
  const everyone = people.map((_, seat) => seat);
  const assignments: Record<string, number[]> = {};
  const charges: SplitRequest["charges"] = {};
  for (const line of doc.lines ?? []) {
    if (line.kind === "item" || line.kind === "discount") assignments[String(line.row)] = everyone;
    else if (line.kind === "fee" || line.kind === "tip" || (line.kind === "tax" && !doc.taxIncluded)) {
      charges[String(line.row)] = { mode: "proportional" };
    }
  }
  return {
    members: people.map((p) => (p.self ? { name: p.name, self: true } : { name: p.name })),
    assignments,
    charges,
    expectedVersion: doc.split?.version ?? 0,
    expectedUpdatedAt: doc.updatedAt ?? "",
    ...(paidBy !== undefined ? { paidBy } : {}),
  };
}
