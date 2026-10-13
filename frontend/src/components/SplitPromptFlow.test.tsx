import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DINNER, fakeApi } from "../test/fakeApi";
import { Home } from "./Home";

const list = (status: string) => ({
  documents: [{ docId: DINNER.docId, status, vendor: "THAI BASIL", totalCents: 2886, createdAt: "x" }],
  review: { reviewed: 0, acceptedUnchanged: 0, acceptanceRate: null },
});

function renderHome(api = fakeApi()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <Home status="signedIn" api={api} onSignIn={vi.fn()} onSignOut={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe("Looks right → Split this bill? (inside Home)", () => {
  it("after a scan opens by itself", async () => {
    const api = fakeApi({
      listDocuments: vi.fn().mockResolvedValueOnce({ ...list("x"), documents: [] }).mockResolvedValue(list("ready")),
      uploadDocument: vi.fn().mockResolvedValue(DINNER.docId),
      // "Everything looks right" shows when something needs a look; a clean bill shows Split bill.
      getDocument: vi.fn().mockResolvedValue({ ...DINNER, status: "needs_review" }),
      reviewDocument: vi.fn().mockResolvedValue({ ...DINNER, reviewOutcome: "accepted" }),
    });
    renderHome(api);
    await screen.findByText(/nothing yet/i);
    await userEvent.upload(screen.getByLabelText(/upload a photo or pdf/i), new File(["x"], "d.jpg", { type: "image/jpeg" }));

    await userEvent.click(await screen.findByRole("button", { name: /looks right/i }));

    expect(await screen.findByText(/split this bill\?/i)).toBeInTheDocument();
  });

  it("after opening a receipt from Recent", async () => {
    const api = fakeApi({
      listDocuments: vi.fn().mockResolvedValue(list("ready")),
      // "Everything looks right" shows when something needs a look; a clean bill shows Split bill.
      getDocument: vi.fn().mockResolvedValue({ ...DINNER, status: "needs_review" }),
      reviewDocument: vi.fn().mockResolvedValue({ ...DINNER, reviewOutcome: "accepted" }),
    });
    renderHome(api);

    await userEvent.click(await screen.findByText("THAI BASIL"));
    await userEvent.click(await screen.findByRole("button", { name: /looks right/i }));

    expect(await screen.findByText(/split this bill\?/i)).toBeInTheDocument();
  });
});
