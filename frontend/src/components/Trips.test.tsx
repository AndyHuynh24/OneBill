import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { DINNER, fakeApi, TRIP, TRIP_DETAIL } from "../test/fakeApi";
import { StartTrip } from "./StartTrip";
import { TripCard } from "./TripCard";
import { TripSheet } from "./TripSheet";

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("TripCard", () => {
  it("shows the active trip with its receipts and total", async () => {
    const onOpen = vi.fn();
    render(<TripCard active={TRIP} past={[]} onOpen={onOpen} onStart={vi.fn()} />);

    const card = screen.getByRole("button", { name: /chicago/i });
    expect(card).toHaveTextContent("2 receipts · $150.00");
    await userEvent.click(card);
    expect(onOpen).toHaveBeenCalledWith("trip-1");
  });

  it("offers to start a trip when none is on, and lists past trips", async () => {
    const onStart = vi.fn();
    const past = [{ ...TRIP, tripId: "t0", name: "Denver", status: "ended" as const }];
    render(<TripCard active={null} past={past} onOpen={vi.fn()} onStart={onStart} />);

    await userEvent.click(screen.getByRole("button", { name: /start a trip/i }));
    expect(onStart).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: /denver/i })).toBeInTheDocument();
  });
});

describe("StartTrip", () => {
  it("starts a trip with a name and the people", async () => {
    const api = fakeApi();
    const onClose = vi.fn();
    wrap(<StartTrip api={api} onClose={onClose} />);

    await userEvent.type(screen.getByLabelText(/trip name/i), "Chicago");
    await userEvent.type(screen.getByLabelText(/add names/i), "Minh Linh");
    await userEvent.click(screen.getByRole("button", { name: /continue with 3 people/i }));

    expect(api.startTrip).toHaveBeenCalledWith({
      name: "Chicago",
      members: [{ name: "Me", self: true }, { name: "Minh" }, { name: "Linh" }],
    });
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("asks for a name first", async () => {
    const api = fakeApi();
    wrap(<StartTrip api={api} onClose={vi.fn()} />);

    await userEvent.type(screen.getByLabelText(/add names/i), "Minh");
    await userEvent.click(screen.getByRole("button", { name: /continue with 2 people/i }));

    expect(screen.getByRole("alert")).toHaveTextContent(/name/i);
    expect(api.startTrip).not.toHaveBeenCalled();
  });
});

describe("TripSheet", () => {
  it("shows the settle-up list, each person's share, and the receipts", async () => {
    wrap(<TripSheet api={fakeApi()} tripId="trip-1" onClose={vi.fn()} onOpenDocument={vi.fn()} />);

    const settle = await screen.findByRole("list", { name: /settle up/i });
    expect(within(settle).getByRole("listitem", { name: /linh pays me/i })).toHaveTextContent("$40.00");
    expect(within(settle).getByRole("listitem", { name: /linh pays minh/i })).toHaveTextContent("$10.00");
    expect(screen.getByRole("list", { name: /each person/i })).toHaveTextContent("Minh$50.00");
    expect(screen.getByRole("button", { name: /thai basil/i })).toHaveTextContent("paid by Me");
  });

  it("ticks a payment as paid", async () => {
    const api = fakeApi();
    wrap(<TripSheet api={api} tripId="trip-1" onClose={vi.fn()} onOpenDocument={vi.fn()} />);

    await userEvent.click(await screen.findByRole("button", { name: "Mark Linh pays Me as paid" }));

    expect(api.setTripSettled).toHaveBeenCalledWith("trip-1", { from: "Linh", to: "Me", paid: true });
  });

  it("splits an unsplit receipt evenly among the trip's people in one tap", async () => {
    const api = fakeApi({
      getTrip: vi.fn().mockResolvedValue({ ...TRIP_DETAIL, unsplit: ["dinner"] }),
      getDocument: vi.fn().mockResolvedValue({ ...DINNER, docId: "dinner" }),
    });
    wrap(<TripSheet api={api} tripId="trip-1" onClose={vi.fn()} onOpenDocument={vi.fn()} />);

    await userEvent.click(await screen.findByRole("button", { name: /split thai basil evenly/i }));

    await vi.waitFor(() => expect(api.saveSplit).toHaveBeenCalled());
    const [docId, request] = vi.mocked(api.saveSplit).mock.calls[0];
    expect(docId).toBe("dinner");
    expect(request.members.map((m) => m.name)).toEqual(["Me", "Minh", "Linh"]);
  });

  it("opens a receipt", async () => {
    const onOpenDocument = vi.fn();
    wrap(<TripSheet api={fakeApi()} tripId="trip-1" onClose={vi.fn()} onOpenDocument={onOpenDocument} />);

    await userEvent.click(await screen.findByRole("button", { name: /shell/i }));

    expect(onOpenDocument).toHaveBeenCalledWith("gas");
  });

  it("ends the trip", async () => {
    const api = fakeApi();
    wrap(<TripSheet api={api} tripId="trip-1" onClose={vi.fn()} onOpenDocument={vi.fn()} />);

    await userEvent.click(await screen.findByRole("button", { name: /end trip/i }));

    expect(api.updateTrip).toHaveBeenCalledWith("trip-1", { end: true });
  });

  it("deletes the trip only after confirming, keeping its receipts", async () => {
    const api = fakeApi();
    const onClose = vi.fn();
    wrap(<TripSheet api={api} tripId="trip-1" onClose={onClose} onOpenDocument={vi.fn()} />);

    await userEvent.click(await screen.findByRole("button", { name: /delete trip/i }));
    expect(api.deleteTrip).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /yes, delete/i }));

    expect(api.deleteTrip).toHaveBeenCalledWith("trip-1");
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
