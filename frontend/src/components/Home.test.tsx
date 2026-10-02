import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type OneTallyApi } from "../api";
import { NETFLIX, fakeApi } from "../test/fakeApi";
import { type AuthStatus, Home } from "./Home";

function renderHome(status: AuthStatus, api: OneTallyApi = fakeApi()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const props = { status, api, onSignIn: vi.fn(), onSignOut: vi.fn() };
  render(
    <QueryClientProvider client={client}>
      <Home {...props} />
    </QueryClientProvider>,
  );
  return props;
}

const DOCS = {
  documents: [
    { docId: "d3", status: "processing", filename: "new.jpg", createdAt: "x" },
    { docId: "d2", status: "needs_review", vendor: "PG&E", totalCents: 8410, documentDate: "2026-09-20", createdAt: "x" },
    { docId: "d1", status: "ready", vendor: "Target", totalCents: 2618, documentDate: "2026-09-28", createdAt: "x" },
  ],
  review: { reviewed: 0, acceptedUnchanged: 0, acceptanceRate: null },
};

describe("Home — signed out", () => {
  it("shows a loading state while the session is checked", () => {
    renderHome("loading");

    expect(screen.getByText(/checking your session/i)).toBeInTheDocument();
  });

  it("offers Sign in", async () => {
    const props = renderHome("signedOut");

    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(props.onSignIn).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: /scan/i })).not.toBeInTheDocument();
  });
});

describe("Home — signed in", () => {
  it("shows one primary action: the Scan button", async () => {
    renderHome("signedIn");

    expect(await screen.findByRole("button", { name: /scan/i })).toBeInTheDocument();
  });

  it("lists recent documents with readable statuses", async () => {
    renderHome("signedIn", fakeApi({ listDocuments: vi.fn().mockResolvedValue(DOCS) }));

    expect(await screen.findByText("Target")).toBeInTheDocument();
    expect(screen.getByText("Reading…")).toBeInTheDocument();
    expect(screen.getByText("Check this")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText(/\$84\.10/)).toBeInTheDocument();
  });

  it("lists upcoming deadlines", async () => {
    const api = fakeApi({
      listObligations: vi.fn().mockResolvedValue({
        obligations: [{ oblId: "o1", docId: "d1", kind: "return_by", dueDate: "2026-10-28", vendor: "Target" }],
      }),
    });
    renderHome("signedIn", api);

    expect(await screen.findByText(/Return by: Target/)).toBeInTheDocument();
    expect(api.listObligations).toHaveBeenCalledWith(expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/));
  });

  it("shows friendly empty states", async () => {
    renderHome("signedIn");

    expect(await screen.findByText(/tap scan to add your first/i)).toBeInTheDocument();
    expect(screen.getByText(/no deadlines/i)).toBeInTheDocument();
  });

  it("uploads the scanned file", async () => {
    const api = fakeApi();
    renderHome("signedIn", api);
    const file = new File(["jpeg"], "receipt.jpg", { type: "image/jpeg" });

    await userEvent.upload(await screen.findByLabelText(/scan or upload/i), file);

    expect(api.uploadDocument).toHaveBeenCalledWith(file);
  });

  it("explains when uploads are invite-only", async () => {
    const api = fakeApi({
      uploadDocument: vi.fn().mockRejectedValue(new ApiError("Uploads are invite-only for now.", 403)),
    });
    renderHome("signedIn", api);

    await userEvent.upload(
      await screen.findByLabelText(/scan or upload/i),
      new File(["x"], "a.jpg", { type: "image/jpeg" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Uploads are invite-only for now.");
  });

  it("opens a document's details", async () => {
    const api = fakeApi({ listDocuments: vi.fn().mockResolvedValue(DOCS) });
    renderHome("signedIn", api);

    await userEvent.click(await screen.findByText("Target"));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(api.getDocument).toHaveBeenCalledWith("d1");
  });

  it("refreshes deadlines once when a document finishes, instead of polling them", async () => {
    const processing = { ...DOCS, documents: [DOCS.documents[0]] }; // one doc being read
    const finished = {
      ...DOCS,
      documents: [{ ...DOCS.documents[0], status: "ready", vendor: "New Shop" }],
    };
    const api = fakeApi({
      listDocuments: vi.fn().mockResolvedValueOnce(processing).mockResolvedValue(finished),
    });
    renderHome("signedIn", api);
    expect(await screen.findByText("Reading…")).toBeInTheDocument();
    expect(api.listObligations).toHaveBeenCalledTimes(1);

    // Any documents refresh (here: after an upload) that shows the doc finished…
    await userEvent.upload(
      screen.getByLabelText(/scan or upload/i),
      new File(["x"], "a.jpg", { type: "image/jpeg" }),
    );

    expect(await screen.findByText("New Shop")).toBeInTheDocument();
    // …refreshes the deadlines exactly once more.
    await vi.waitFor(() => expect(api.listObligations).toHaveBeenCalledTimes(2));
  });

  it("opens a scan automatically once it has been read", async () => {
    const empty = { ...DOCS, documents: [] };
    const read = {
      ...DOCS,
      documents: [{ docId: "new-doc", status: "needs_review", vendor: "Thai Basil", createdAt: "x" }],
    };
    const api = fakeApi({
      listDocuments: vi.fn().mockResolvedValueOnce(empty).mockResolvedValue(read),
      uploadDocument: vi.fn().mockResolvedValue("new-doc"),
    });
    renderHome("signedIn", api);
    expect(await screen.findByText(/nothing yet/i)).toBeInTheDocument();

    await userEvent.upload(
      screen.getByLabelText(/scan or upload/i),
      new File(["x"], "dinner.jpg", { type: "image/jpeg" }),
    );

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(api.getDocument).toHaveBeenCalledWith("new-doc");
  });

  it("doesn't reopen documents that were already there", async () => {
    renderHome("signedIn", fakeApi({ listDocuments: vi.fn().mockResolvedValue(DOCS) }));

    expect(await screen.findByText("Target")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows subscriptions with next month's total, and opens one", async () => {
    const api = fakeApi({
      listSubscriptions: vi.fn().mockResolvedValue({
        subscriptions: [NETFLIX],
        connection: { connected: true, createdAt: "x", lastUsedAt: null },
      }),
    });
    renderHome("signedIn", api);

    expect(await screen.findByRole("heading", { name: /subscriptions/i })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /netflix/i }));

    expect(await screen.findByRole("dialog", { name: "Netflix" })).toBeInTheDocument();
  });

  it("Connect Gmail opens the setup sheet", async () => {
    renderHome("signedIn");

    await userEvent.click(await screen.findByRole("button", { name: /connect gmail/i }));

    expect(await screen.findByRole("dialog", { name: /connect gmail/i })).toBeInTheDocument();
  });

  it("signs out", async () => {
    const props = renderHome("signedIn");

    await userEvent.click(await screen.findByRole("button", { name: /sign out/i }));

    expect(props.onSignOut).toHaveBeenCalledOnce();
  });
});
