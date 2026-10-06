import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { OneBillApi } from "../api";
import { DINNER, RECEIPT, fakeApi } from "../test/fakeApi";
import { DocumentSheet } from "./DocumentSheet";

function renderSheet(api: OneBillApi, onClose = vi.fn(), docId = RECEIPT.docId) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DocumentSheet api={api} docId={docId} onClose={onClose} />
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
    expect(screen.getByText(/Payment due/)).toBeInTheDocument();
    expect(screen.getByText(/Oct 28, 2026/)).toBeInTheDocument();
  });

  it("'Looks right' accepts the extraction, then offers to split (any bill with items)", async () => {
    const api = fakeApi();
    const { onClose } = renderSheet(api);

    await userEvent.click(await screen.findByRole("button", { name: /looks right/i }));

    expect(api.reviewDocument).toHaveBeenCalledWith(RECEIPT.docId, { action: "accept" });
    expect(await screen.findByText(/split this bill\?/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /just me/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("vendor and total are fixed in place (a bill without lines)", async () => {
    const noLines = { ...RECEIPT, lines: undefined };
    const api = fakeApi({
      getDocument: vi.fn().mockResolvedValue(noLines),
      reviewDocument: vi.fn().mockResolvedValue(noLines),
    });
    renderSheet(api);

    await userEvent.click(await screen.findByRole("button", { name: /edit vendor/i }));
    await userEvent.type(screen.getByLabelText("Vendor"), "{Control>}a{/Control}Target{Enter}");
    expect(api.reviewDocument).toHaveBeenCalledWith(RECEIPT.docId, { action: "correct", fields: { vendor: "Target" } });

    await userEvent.click(await screen.findByRole("button", { name: /edit total/i }));
    const total = screen.getByLabelText("Total");
    await userEvent.clear(total);
    await userEvent.type(total, "26.81{Enter}");
    expect(api.reviewDocument).toHaveBeenLastCalledWith(RECEIPT.docId, {
      action: "correct",
      fields: { totalCents: 2681 },
    });
  });

  it("can change a deadline date in place", async () => {
    const api = fakeApi();
    renderSheet(api);

    await userEvent.click(await screen.findByRole("button", { name: /payment due: oct 28, 2026/i }));
    const due = screen.getByLabelText(/deadline date/i);
    await userEvent.clear(due);
    await userEvent.type(due, "2026-11-15");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(api.reviewDocument).toHaveBeenCalledWith(RECEIPT.docId, {
      action: "correct",
      fields: { deadlines: [{ kind: "payment_due", dueDate: "2026-11-15" }] },
    });
  });

  it("tapping a line edits it in place and sends every line (rows are numbered by the server)", async () => {
    const api = fakeApi();
    renderSheet(api);

    await userEvent.click(await screen.findByRole("button", { name: /edit amount of bananas/i }));
    const amount = screen.getByLabelText("Amount");
    await userEvent.clear(amount);
    await userEvent.type(amount, "1.92{Enter}");

    expect(vi.mocked(api.reviewDocument).mock.calls[0][1]).toEqual({
      action: "correct",
      fields: {
        lines: [
          { description: "BANANAS", kind: "item", amountCents: 192 },
          { description: "COUPON", kind: "discount", amountCents: -200 },
          { description: "SUBTOTAL", kind: "subtotal", amountCents: 2448 },
          { description: "TAX", kind: "tax", amountCents: 170 },
          { description: "TOTAL", kind: "total", amountCents: 2618 },
          { description: "CASH", kind: "payment", amountCents: 3000 },
          { description: "CHANGE", kind: "change", amountCents: 382 },
        ],
      },
    });
  });

  it("can remove a line, change a line's kind, and add a missing item", async () => {
    const api = fakeApi();
    renderSheet(api);
    const sent = () => (vi.mocked(api.reviewDocument).mock.lastCall![1] as { fields: { lines: unknown[] } }).fields.lines;

    await userEvent.click(await screen.findByRole("button", { name: /^edit change$/i }));
    await userEvent.click(screen.getByRole("button", { name: /remove line/i }));
    expect(sent()).toHaveLength(6);

    await userEvent.click(await screen.findByRole("button", { name: /^edit cash$/i }));
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "info");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
    expect(sent()[5]).toMatchObject({ description: "CASH", kind: "info" });

    await userEvent.click(screen.getByRole("button", { name: /add item/i }));
    await userEvent.type(screen.getByLabelText("Name"), "MILK");
    await userEvent.type(screen.getByLabelText("Amount"), "4.19{Enter}");
    expect(sent()).toHaveLength(8);
    expect(sent()[2]).toEqual({ description: "MILK", kind: "item", amountCents: 419 }); // after the items
  });

  it("an unreadable amount is explained instead of saved", async () => {
    const api = fakeApi();
    renderSheet(api);

    await userEvent.click(await screen.findByRole("button", { name: /edit amount of tax/i }));
    const tax = screen.getByLabelText("Amount");
    await userEvent.clear(tax);
    await userEvent.type(tax, "one seventy{Enter}");

    expect(screen.getByRole("alert")).toHaveTextContent(/amount like/i);
    expect(api.reviewDocument).not.toHaveBeenCalled();
  });

  it("rejects an invalid total without calling the API", async () => {
    const api = fakeApi({ getDocument: vi.fn().mockResolvedValue({ ...RECEIPT, lines: undefined }) });
    renderSheet(api);

    await userEvent.click(await screen.findByRole("button", { name: /edit total/i }));
    const total = screen.getByLabelText("Total");
    await userEvent.clear(total);
    await userEvent.type(total, "twenty{Enter}");

    expect(screen.getByRole("alert")).toHaveTextContent(/total/i);
    expect(api.reviewDocument).not.toHaveBeenCalled();
  });

  it("a flagged line can be fixed with one tap on the suggested amount", async () => {
    const api = fakeApi({
      getDocument: vi.fn().mockResolvedValue({
        ...RECEIPT,
        confidence: { level: "check", checks: [{ row: 4, reason: "One digit may be misread", suggestedCents: 180 }] },
      }),
    });
    renderSheet(api);

    await userEvent.click(await screen.findByRole("button", { name: /use \$1\.80/i }));

    const { fields } = vi.mocked(api.reviewDocument).mock.calls[0][1] as { fields: { lines: unknown[] } };
    expect(fields.lines[3]).toEqual({ description: "TAX", kind: "tax", amountCents: 180 });
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

  it("after 'Looks right' on a bill that adds up, asks whether to split it", async () => {
    const api = fakeApi({
      getDocument: vi.fn().mockResolvedValue({ ...DINNER, status: "needs_review" }),
      reviewDocument: vi.fn().mockResolvedValue(DINNER),
    });
    const { onClose } = renderSheet(api, vi.fn(), DINNER.docId);

    await userEvent.click(await screen.findByRole("button", { name: /looks right/i }));

    expect(await screen.findByText(/split this bill\?/i)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /just me/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("'Split with friends' opens Who's here", async () => {
    const api = fakeApi({ getDocument: vi.fn().mockResolvedValue(DINNER) });
    renderSheet(api, vi.fn(), DINNER.docId);

    await userEvent.click(await screen.findByRole("button", { name: /split bill/i }));

    expect(await screen.findByRole("heading", { name: /who's here/i })).toBeInTheDocument();
  });

  it("a bill that doesn't add up can still be split (on its recorded items)", async () => {
    renderSheet(fakeApi({ getDocument: vi.fn().mockResolvedValue({ ...RECEIPT, status: "ready" }) }));

    expect(await screen.findByRole("button", { name: /split bill/i })).toBeEnabled();
  });

  it("Delete removes the document after a confirm", async () => {
    const api = fakeApi({ getDocument: vi.fn().mockResolvedValue(DINNER) });
    const onClose = vi.fn();
    renderSheet(api, onClose, DINNER.docId);

    await userEvent.click(await screen.findByRole("button", { name: /^delete bill$/i }));
    await userEvent.click(screen.getByRole("button", { name: /yes, delete/i }));

    expect(api.deleteDocument).toHaveBeenCalledWith(DINNER.docId);
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
