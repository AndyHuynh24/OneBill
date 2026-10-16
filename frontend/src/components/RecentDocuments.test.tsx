import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { DocumentSummary } from "../api";
import { RecentDocuments } from "./RecentDocuments";

const BILL: DocumentSummary = {
  docId: "d1",
  status: "ready",
  vendor: "THAI BASIL",
  totalCents: 4556,
  currency: "USD",
  createdAt: "2026-10-01T18:00:00Z",
};

describe("RecentDocuments", () => {
  it("says who owes you for money you lent, then that they paid you back", () => {
    const lent: DocumentSummary = { ...BILL, docId: "o1", vendor: "Concert ticket", kind: "owed", person: "Minh", totalCents: 5000, owedCents: 5000 };
    const { rerender } = render(<RecentDocuments documents={[lent]} uploading={[]} onOpen={vi.fn()} />);

    expect(screen.getByRole("button", { name: /minh owes you/i })).toHaveTextContent("Concert ticket");

    rerender(<RecentDocuments documents={[{ ...lent, owedCents: 0 }]} uploading={[]} onOpen={vi.fn()} />);
    expect(screen.getByRole("button", { name: /minh paid you back/i })).toBeInTheDocument();
  });

  it("shows what's still owed on a split bill", () => {
    render(<RecentDocuments documents={[{ ...BILL, owedCents: 2105 }]} uploading={[]} onOpen={vi.fn()} />);

    expect(screen.getByText("Owed $21.05")).toBeInTheDocument();
  });

  it("shows nothing once everyone has paid, or for bills that aren't split", () => {
    render(
      <RecentDocuments
        documents={[{ ...BILL, owedCents: 0 }, { ...BILL, docId: "d2" }]}
        uploading={[]}
        onOpen={vi.fn()}
      />,
    );

    expect(screen.queryByText(/owed/i)).not.toBeInTheDocument();
  });
});
