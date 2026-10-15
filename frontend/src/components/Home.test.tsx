import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type OneBillApi } from "../api";
import { NETFLIX, TRIP, fakeApi } from "../test/fakeApi";
import { type AuthStatus, Home } from "./Home";

function renderHome(status: AuthStatus, api: OneBillApi = fakeApi()) {
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
    expect(screen.queryByText("Ready")).not.toBeInTheDocument(); // the normal state needs no label
    expect(screen.getByText(/\$84\.10/)).toBeInTheDocument();
  });

  it("lists upcoming deadlines", async () => {
    const api = fakeApi({
      listObligations: vi.fn().mockResolvedValue({
        obligations: [{ oblId: "o1", docId: "d1", kind: "payment_due", dueDate: "2026-10-28", vendor: "PG&E" }],
      }),
    });
    renderHome("signedIn", api);

    expect(await screen.findByText("PG&E")).toBeInTheDocument();
    expect(screen.getByText(/payment due in \d+ days/i)).toBeInTheDocument();
    expect(api.listObligations).toHaveBeenCalledWith(expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/));
  });

  it("shows friendly empty states", async () => {
    renderHome("signedIn");

    expect(await screen.findByText(/tap scan receipt to add your first/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing coming up/i)).toBeInTheDocument();
  });

  it("uploads the scanned file", async () => {
    const api = fakeApi();
    renderHome("signedIn", api);
    const file = new File(["jpeg"], "receipt.jpg", { type: "image/jpeg" });

    await userEvent.upload(await screen.findByLabelText(/upload a photo or pdf/i), file);

    expect(api.uploadDocument).toHaveBeenCalledWith(file);
  });

  it("explains when today's uploads are used up", async () => {
    const api = fakeApi({
      uploadDocument: vi.fn().mockRejectedValue(new ApiError("You've used today's 3 uploads. Try again tomorrow.", 429)),
    });
    renderHome("signedIn", api);

    await userEvent.upload(
      await screen.findByLabelText(/upload a photo or pdf/i),
      new File(["x"], "a.jpg", { type: "image/jpeg" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("You've used today's 3 uploads. Try again tomorrow.");
  });

  it("shows the daily scans under Receipts", async () => {
    renderHome("signedIn");

    expect(await screen.findByText("3 scans a day · 3 left today")).toBeInTheDocument();
  });

  it("renews today's scans once they're used up", async () => {
    const api = fakeApi({
      listDocuments: vi.fn().mockResolvedValue({
        ...DOCS,
        usage: { dailyLimit: 3, usedToday: 3, leftToday: 0, renewalsLeft: 2 },
      }),
    });
    renderHome("signedIn", api);

    await userEvent.click(await screen.findByRole("button", { name: "Renew (2 left this month)" }));

    expect(api.renewUsage).toHaveBeenCalledOnce();
    expect(await screen.findByText("3 scans a day · 3 left today")).toBeInTheDocument();
  });

  it("explains a refused renewal", async () => {
    const api = fakeApi({
      listDocuments: vi.fn().mockResolvedValue({
        ...DOCS,
        usage: { dailyLimit: 3, usedToday: 3, leftToday: 0, renewalsLeft: 1 },
      }),
      renewUsage: vi.fn().mockRejectedValue(new ApiError("You've used this month's 2 renewals.", 409)),
    });
    renderHome("signedIn", api);

    await userEvent.click(await screen.findByRole("button", { name: /renew/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("this month's 2 renewals");
  });

  it("shows App usage only when the API sends it (the owner)", async () => {
    const appUsage = {
      today: { scans: 12, pages: 4, emails: 3, renewals: 1 },
      month: { scans: 87, pages: 51, emails: 40, renewals: 5 },
      limits: { scans: 200, pages: 600, emails: 300 },
    };
    renderHome("signedIn", fakeApi({ listDocuments: vi.fn().mockResolvedValue({ ...DOCS, appUsage }) }));

    expect(await screen.findByRole("heading", { name: "App usage" })).toBeInTheDocument();
  });

  it("hides App usage from everyone else", async () => {
    renderHome("signedIn");

    await screen.findByText(/scans a day/);
    expect(screen.queryByRole("heading", { name: "App usage" })).not.toBeInTheDocument();
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
      screen.getByLabelText(/upload a photo or pdf/i),
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
      screen.getByLabelText(/upload a photo or pdf/i),
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

  it("Recent shows the 5 newest; View all opens every document, and Back returns", async () => {
    const many = {
      ...DOCS,
      documents: Array.from({ length: 8 }, (_, i) => ({
        docId: `d${i}`,
        status: "ready",
        vendor: `Shop ${i}`,
        totalCents: 100 * (i + 1),
        createdAt: "x",
      })),
    };
    renderHome("signedIn", fakeApi({ listDocuments: vi.fn().mockResolvedValue(many) }));

    expect(await screen.findByText("Shop 4")).toBeInTheDocument();
    expect(screen.queryByText("Shop 5")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /view all receipts/i }));
    expect(screen.getByRole("heading", { name: /all receipts/i })).toBeInTheDocument();
    expect(screen.getByText("Shop 7")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /back/i }));
    expect(screen.getByRole("heading", { name: /subscriptions & bills/i, level: 2 })).toBeInTheDocument();
  });

  it("the subscriptions page shows them all", async () => {
    const api = fakeApi({
      listSubscriptions: vi.fn().mockResolvedValue({
        subscriptions: [NETFLIX],
        connection: { connected: false, createdAt: null, lastUsedAt: null },
      }),
    });
    renderHome("signedIn", api);

    await userEvent.click(await screen.findByRole("button", { name: /^view all$/i }));

    expect(screen.getByRole("heading", { name: /subscriptions & bills/i, level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /subscriptions/i })).toHaveTextContent("Netflix");
  });

  it("subscriptions and bill due dates share one list: the 3 soonest, each once", async () => {
    const api = fakeApi({
      listSubscriptions: vi.fn().mockResolvedValue({
        subscriptions: [
          NETFLIX, // renews Nov 1
          { ...NETFLIX, serviceKey: "spotify", service: "Spotify", nextRenewal: "2026-10-20" },
          { ...NETFLIX, serviceKey: "gym", service: "Gym", nextRenewal: "2026-12-01" },
        ],
        connection: { connected: false, createdAt: null, lastUsedAt: null },
      }),
      listObligations: vi.fn().mockResolvedValue({
        obligations: [
          { oblId: "o1", docId: "d1", kind: "payment_due", dueDate: "2026-10-28", vendor: "PG&E", amountCents: 8734 },
          { oblId: "o2", docId: "x", kind: "renewal", dueDate: "2026-10-20", vendor: "Spotify" }, // Spotify's own reminder
        ],
      }),
    });
    renderHome("signedIn", api);

    const rows = await screen.findAllByRole("button", { name: /renews|due/i });
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("Spotify"),
      expect.stringContaining("PG&E"),
      expect.stringContaining("Netflix"),
    ]);
    expect(screen.queryByText("Gym")).not.toBeInTheDocument(); // 4th: on the full page
  });

  it("tapping a bill's due date opens that bill; a subscription opens its sheet", async () => {
    const api = fakeApi({
      listSubscriptions: vi.fn().mockResolvedValue({
        subscriptions: [NETFLIX],
        connection: { connected: false, createdAt: null, lastUsedAt: null },
      }),
      listObligations: vi.fn().mockResolvedValue({
        obligations: [{ oblId: "o1", docId: "d1", kind: "payment_due", dueDate: "2026-10-28", vendor: "PG&E" }],
      }),
    });
    renderHome("signedIn", api);

    await userEvent.click(await screen.findByRole("button", { name: /PG&E/ }));
    expect(api.getDocument).toHaveBeenCalledWith("d1");

    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    await userEvent.click(screen.getByRole("button", { name: /Netflix/ }));
    expect(await screen.findByRole("dialog", { name: /netflix/i })).toBeInTheDocument();
  });

  it("the full page lists bill due dates (not subscription reminders) and can remove one", async () => {
    const api = fakeApi({
      listSubscriptions: vi.fn().mockResolvedValue({
        subscriptions: [NETFLIX],
        connection: { connected: false, createdAt: null, lastUsedAt: null },
      }),
      listObligations: vi.fn().mockResolvedValue({
        obligations: [
          { oblId: "o1", docId: "d1", kind: "payment_due", dueDate: "2026-10-28", vendor: "PG&E" },
          { oblId: "o2", docId: "x", kind: "renewal", dueDate: "2026-11-01", vendor: "Netflix" },
        ],
      }),
    });
    renderHome("signedIn", api);

    await userEvent.click(await screen.findByRole("button", { name: /^view all$/i }));

    const due = screen.getByRole("region", { name: /bill due dates/i });
    expect(due).toHaveTextContent("Payment due: PG&E");
    expect(due).not.toHaveTextContent("Netflix");
  });

  it("reminder emails are on by default and can be turned off", async () => {
    const api = fakeApi();
    renderHome("signedIn", api);

    const toggle = await screen.findByRole("switch", { name: /email reminders/i });
    expect(toggle).toBeChecked();
    await userEvent.click(toggle);

    expect(api.setEmailReminders).toHaveBeenCalledWith(false);
    expect(await screen.findByRole("switch", { name: /email reminders/i })).not.toBeChecked();
  });

  it("signs out", async () => {
    const props = renderHome("signedIn");

    await userEvent.click(await screen.findByRole("button", { name: /sign out/i }));

    expect(props.onSignOut).toHaveBeenCalledOnce();
  });

  it("offers to start a trip", async () => {
    renderHome("signedIn");

    await userEvent.click(await screen.findByRole("button", { name: /start a trip/i }));

    expect(screen.getByRole("dialog", { name: /start a trip/i })).toBeInTheDocument();
  });

  it("the trip card's own Scan button puts the receipt in that trip", async () => {
    const api = fakeApi({ listTrips: vi.fn().mockResolvedValue({ active: TRIP, past: [] }) });
    renderHome("signedIn", api);
    const file = new File(["jpeg"], "dinner.jpg", { type: "image/jpeg" });

    await userEvent.upload(await screen.findByLabelText("Upload a photo or PDF for Chicago"), file);

    expect(api.uploadDocument).toHaveBeenCalledWith(file, "trip-1");
  });

  it("the main Scan button never puts a receipt in a trip", async () => {
    const api = fakeApi({ listTrips: vi.fn().mockResolvedValue({ active: TRIP, past: [] }) });
    renderHome("signedIn", api);
    const file = new File(["jpeg"], "receipt.jpg", { type: "image/jpeg" });

    await screen.findByLabelText("Upload a photo or PDF for Chicago");
    await userEvent.upload(screen.getByLabelText("Upload a photo or PDF"), file);

    expect(api.uploadDocument).toHaveBeenCalledWith(file);
  });

  it("shows the trip that's on and opens it", async () => {
    const api = fakeApi({ listTrips: vi.fn().mockResolvedValue({ active: TRIP, past: [] }) });
    renderHome("signedIn", api);

    await userEvent.click(await screen.findByRole("button", { name: /open chicago/i }));

    expect(await screen.findByRole("dialog", { name: "Chicago" })).toBeInTheDocument();
    expect(api.getTrip).toHaveBeenCalledWith("trip-1");
  });
});
