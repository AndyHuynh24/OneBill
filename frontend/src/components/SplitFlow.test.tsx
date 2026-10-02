import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type DocumentDetail, type OneTallyApi } from "../api";
import { DINNER, fakeApi } from "../test/fakeApi";
import { SplitFlow } from "./SplitFlow";

const SAVED = {
  version: 1,
  updatedAt: "2026-10-01T19:10:00+00:00",
  totalCents: 2886,
  members: [
    { id: "m0", name: "Me", self: true, color: 0 },
    { id: "m1", name: "Bao", self: false, color: 1 },
  ],
  assignments: { "1": ["m1"], "2": ["m0"] },
  charges: { "4": { mode: "proportional" } },
  shares: [
    { member: "m0", totalCents: 1307, itemsCents: 1200, extrasCents: 107, rows: [2] },
    { member: "m1", totalCents: 1579, itemsCents: 1450, extrasCents: 129, rows: [1] },
  ],
};

function renderFlow(api: OneTallyApi, doc: DocumentDetail = DINNER, onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SplitFlow api={api} doc={doc} onCancel={vi.fn()} onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

async function splitWithBao() {
  await userEvent.type(screen.getByLabelText(/add names/i), "Bao{Enter}");
  await userEvent.click(screen.getByRole("button", { name: /next/i }));
  await userEvent.click(screen.getByRole("button", { name: /^bao/i })); // PAD THAI
  await userEvent.click(screen.getByRole("button", { name: /^me/i })); // CURRY
  await userEvent.click(screen.getByRole("button", { name: /by what each ordered/i })); // TAX
}

describe("SplitFlow", () => {
  it("who's here → assign → tax → saved; sends seats and rows, never amounts", async () => {
    const api = fakeApi({ saveSplit: vi.fn().mockResolvedValue(SAVED) });
    renderFlow(api);

    await splitWithBao();

    expect(api.saveSplit).toHaveBeenCalledWith(DINNER.docId, {
      members: [{ name: "Me", self: true }, { name: "Bao" }],
      assignments: { "1": [1], "2": [0] },
      charges: { "4": { mode: "proportional" } },
      expectedVersion: 0,
      expectedUpdatedAt: DINNER.updatedAt,
    });
    expect(await screen.findByText(/adds up to \$28\.86/i)).toBeInTheDocument();
    // Bao is now a one-tap chip, remembered only in this browser.
    expect(JSON.parse(localStorage.getItem("onetally.friends.u") ?? "[]")).toEqual([
      expect.objectContaining({ name: "Bao", timesUsed: 1 }),
    ]);
  });

  it("a saved split opens on its summary; Edit goes back to the circle with everything kept", async () => {
    const api = fakeApi({ saveSplit: vi.fn().mockResolvedValue({ ...SAVED, version: 2 }) });
    renderFlow(api, { ...DINNER, split: SAVED });

    expect(screen.getByText(/adds up to \$28\.86/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /edit/i }));
    await userEvent.click(screen.getByRole("button", { name: /PAD THAI.*Bao/ }));
    await userEvent.click(screen.getByRole("button", { name: /^me/i })); // give PAD THAI to me
    await userEvent.click(screen.getByRole("button", { name: /^next$/i })); // items done
    await userEvent.click(screen.getByRole("button", { name: /^next$/i })); // tax kept as it was

    expect(api.saveSplit).toHaveBeenCalledWith(
      DINNER.docId,
      expect.objectContaining({
        assignments: { "1": [0], "2": [0] },
        expectedVersion: 1,
        expectedUpdatedAt: SAVED.updatedAt,
      }),
    );
  });

  it("explains a conflicting save instead of losing it silently", async () => {
    const api = fakeApi({
      saveSplit: vi.fn().mockRejectedValue(new ApiError("This split was changed somewhere else. Reload and try again.", 409)),
    });
    renderFlow(api);

    await splitWithBao();

    expect(await screen.findByRole("alert")).toHaveTextContent(/changed somewhere else/i);
  });

  it("Done closes the sheet", async () => {
    const onClose = renderFlow(fakeApi(), { ...DINNER, split: SAVED });

    await userEvent.click(screen.getByRole("button", { name: /^done$/i }));

    expect(onClose).toHaveBeenCalled();
  });
});
