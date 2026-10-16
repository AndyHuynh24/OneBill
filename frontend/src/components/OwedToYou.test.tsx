import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { DocumentSummary } from "../api";
import { OwedToYou } from "./OwedToYou";

const doc = (docId: string, vendor: string, owedBy: { name: string; cents: number }[], extra: Partial<DocumentSummary> = {}): DocumentSummary => ({
  docId,
  status: "ready",
  vendor,
  documentDate: "2026-10-10",
  currency: "USD",
  createdAt: "2026-10-10T00:00:00Z",
  owedCents: owedBy.reduce((sum, o) => sum + o.cents, 0),
  owedBy,
  ...extra,
});

const DOCS = [
  doc("d1", "THAI BASIL", [{ name: "Minh", cents: 2434 }]),
  doc("d2", "Concert ticket", [{ name: "Minh", cents: 5000 }], { kind: "owed", person: "Minh" }),
  doc("d3", "CHIPOTLE", [{ name: "Bao", cents: 1752 }]),
  doc("d4", "TARGET", []),
];

describe("OwedToYou", () => {
  it("isn't shown when nobody owes you", () => {
    const { container } = render(<OwedToYou documents={[DOCS[3]]} onOpenDocument={vi.fn()} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("is one line with the total and who, opening a list by person", async () => {
    const onOpen = vi.fn();
    render(<OwedToYou documents={DOCS} onOpenDocument={onOpen} />);

    const line = screen.getByRole("button", { name: /owed to you/i });
    expect(line).toHaveTextContent("$91.86");
    expect(line).toHaveTextContent("Minh, Bao");

    await userEvent.click(line);
    const minh = screen.getByRole("listitem", { name: "Minh" });
    expect(minh).toHaveTextContent("$74.34");
    expect(within(minh).getByRole("button", { name: /concert ticket/i })).toHaveTextContent("$50.00");

    await userEvent.click(within(minh).getByRole("button", { name: /thai basil/i }));
    expect(onOpen).toHaveBeenCalledWith("d1");
  });

  it("reminds one person of everything they owe, in one message", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText }, share: undefined });
    render(<OwedToYou documents={DOCS} onOpenDocument={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /owed to you/i }));
    await userEvent.click(within(screen.getByRole("listitem", { name: "Minh" })).getByRole("button", { name: /remind minh/i }));

    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("$74.34"));
    expect(writeText.mock.calls[0][0]).toContain("Concert ticket $50.00");
  });
});
