import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { OneTallyApi } from "../api";
import { DINNER, RECEIPT, fakeApi } from "../test/fakeApi";
import { DocumentSheet } from "./DocumentSheet";

function renderSheet(api: OneTallyApi, onClose = vi.fn(), docId = RECEIPT.docId) {
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

  it("'Fix' sends only the fields that changed", async () => {
    const api = fakeApi({ getDocument: vi.fn().mockResolvedValue({ ...RECEIPT, lines: undefined }) });
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
      fields: { deadlines: [{ kind: "payment_due", dueDate: "2026-11-15" }] },
    });
  });

  it("Fix edits individual lines and sends them (rows are numbered by the server)", async () => {
    const api = fakeApi();
    renderSheet(api);

    await userEvent.click(await screen.findByRole("button", { name: /^fix$/i }));
    const bananas = screen.getByLabelText("Line 1 amount");
    await userEvent.clear(bananas);
    await userEvent.type(bananas, "1.92");
    await userEvent.click(screen.getByRole("button", { name: "Remove line 7" })); // CHANGE
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    const fields = vi.mocked(api.reviewDocument).mock.calls[0][1];
    expect(fields).toEqual({
      action: "correct",
      fields: {
        lines: [
          { description: "BANANAS", kind: "item", amountCents: 192 },
          { description: "COUPON", kind: "discount", amountCents: -200 },
          { description: "SUBTOTAL", kind: "subtotal", amountCents: 2448 },
          { description: "TAX", kind: "tax", amountCents: 170 },
          { description: "TOTAL", kind: "total", amountCents: 2618 },
          { description: "CASH", kind: "payment", amountCents: 3000 },
        ],
      },
    });
  });

  it("can add a missing line and change a line's kind", async () => {
    const api = fakeApi();
    renderSheet(api);

    await userEvent.click(await screen.findByRole("button", { name: /^fix$/i }));
    await userEvent.click(screen.getByRole("button", { name: /add line/i }));
    await userEvent.type(screen.getByLabelText("Line 8 description"), "MILK");
    await userEvent.type(screen.getByLabelText("Line 8 amount"), "4.19");
    await userEvent.selectOptions(screen.getByLabelText("Line 6 kind"), "info");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    const { fields } = vi.mocked(api.reviewDocument).mock.calls[0][1] as { fields: { lines: unknown[] } };
    expect(fields.lines).toHaveLength(8);
    expect(fields.lines[5]).toMatchObject({ description: "CASH", kind: "info" });
    expect(fields.lines[7]).toEqual({ description: "MILK", kind: "item", amountCents: 419 });
  });

  it("names the line with an unreadable amount instead of saving", async () => {
    const api = fakeApi();
    renderSheet(api);

    await userEvent.click(await screen.findByRole("button", { name: /^fix$/i }));
    const tax = screen.getByLabelText("Line 4 amount");
    await userEvent.clear(tax);
    await userEvent.type(tax, "one seventy");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(screen.getByRole("alert")).toHaveTextContent(/line 4/i);
    expect(api.reviewDocument).not.toHaveBeenCalled();
  });

  it("rejects an invalid total without calling the API", async () => {
    const api = fakeApi({ getDocument: vi.fn().mockResolvedValue({ ...RECEIPT, lines: undefined }) });
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

    await userEvent.click(await screen.findByRole("button", { name: /^delete$/i }));
    await userEvent.click(screen.getByRole("button", { name: /yes, delete/i }));

    expect(api.deleteDocument).toHaveBeenCalledWith(DINNER.docId);
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
