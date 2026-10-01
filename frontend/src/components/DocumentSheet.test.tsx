import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { OneTallyApi } from "../api";
import { RECEIPT, fakeApi } from "../test/fakeApi";
import { DocumentSheet } from "./DocumentSheet";

function renderSheet(api: OneTallyApi, onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DocumentSheet api={api} docId={RECEIPT.docId} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

describe("DocumentSheet", () => {
  it("lists purchased items separately from summary and payment lines", async () => {
    renderSheet(fakeApi());

    const items = await screen.findByRole("table", { name: /items/i });
    expect(within(items).getByText("BANANAS")).toBeInTheDocument();
    expect(within(items).getByText("COUPON")).toBeInTheDocument();
    expect(within(items).queryByText("TOTAL")).not.toBeInTheDocument();
    expect(within(items).queryByText("CASH")).not.toBeInTheDocument();

    const summary = screen.getByRole("table", { name: /summary/i });
    for (const label of ["SUBTOTAL", "TAX", "TOTAL", "CASH", "CHANGE"]) {
      expect(within(summary).getByText(label)).toBeInTheDocument();
    }
  });

  it("shows why a document needs a look, and its deadlines", async () => {
    renderSheet(fakeApi());

    expect(await screen.findByText(/hard to read/i)).toBeInTheDocument();
    expect(screen.getByText(/Return by/)).toBeInTheDocument();
    expect(screen.getByText(/Oct 28, 2026/)).toBeInTheDocument();
  });

  it("'Looks right' accepts the extraction and closes", async () => {
    const api = fakeApi();
    const { onClose } = renderSheet(api);

    await userEvent.click(await screen.findByRole("button", { name: /looks right/i }));

    expect(api.reviewDocument).toHaveBeenCalledWith(RECEIPT.docId, { action: "accept" });
    expect(onClose).toHaveBeenCalled();
  });

  it("'Fix' sends only the fields that changed", async () => {
    const api = fakeApi();
    renderSheet(api);

    await userEvent.click(await screen.findByRole("button", { name: /^fix$/i }));
    const vendor = screen.getByLabelText(/vendor/i);
    await userEvent.clear(vendor);
    await userEvent.type(vendor, "Target");
    const total = screen.getByLabelText(/total/i);
    await userEvent.clear(total);
    await userEvent.type(total, "26.81");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(api.reviewDocument).toHaveBeenCalledWith(RECEIPT.docId, {
      action: "correct",
      fields: { vendor: "Target", totalCents: 2681 },
    });
  });

  it("can change a deadline date", async () => {
    const api = fakeApi();
    renderSheet(api);

    await userEvent.click(await screen.findByRole("button", { name: /^fix$/i }));
    const due = screen.getByLabelText(/deadline 1 date/i);
    await userEvent.clear(due);
    await userEvent.type(due, "2026-11-15");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(api.reviewDocument).toHaveBeenCalledWith(RECEIPT.docId, {
      action: "correct",
      fields: { deadlines: [{ kind: "return_by", dueDate: "2026-11-15" }] },
    });
  });

  it("rejects an invalid total without calling the API", async () => {
    const api = fakeApi();
    renderSheet(api);

    await userEvent.click(await screen.findByRole("button", { name: /^fix$/i }));
    const total = screen.getByLabelText(/total/i);
    await userEvent.clear(total);
    await userEvent.type(total, "twenty");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(screen.getByRole("alert")).toHaveTextContent(/total/i);
    expect(api.reviewDocument).not.toHaveBeenCalled();
  });

  it("a failed document shows the reason and no review buttons", async () => {
    const api = fakeApi({
      getDocument: vi.fn().mockResolvedValue({
        ...RECEIPT,
        status: "failed",
        failureReason: "We couldn't read this document. Try a clearer, well-lit photo.",
        lines: undefined,
      }),
    });
    renderSheet(api);

    expect(await screen.findByText(/clearer, well-lit photo/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /looks right/i })).not.toBeInTheDocument();
  });

  it("leaves out missing fields instead of showing an error-like message", async () => {
    const api = fakeApi({
      getDocument: vi.fn().mockResolvedValue({ ...RECEIPT, documentDate: null, issues: [] }),
    });
    renderSheet(api);

    expect(await screen.findByRole("table", { name: /items/i })).toBeInTheDocument();
    expect(screen.queryByText(/no date/i)).not.toBeInTheDocument();
  });

  it("closes with the close button", async () => {
    const { onClose } = renderSheet(fakeApi());

    await userEvent.click(await screen.findByRole("button", { name: /close/i }));

    expect(onClose).toHaveBeenCalled();
  });
});
