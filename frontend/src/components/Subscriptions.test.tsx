import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NETFLIX } from "../test/fakeApi";
import { Subscriptions } from "./Subscriptions";

const NOT_CONNECTED = { connected: false, createdAt: null, lastUsedAt: null };
const SPOTIFY = { ...NETFLIX, serviceKey: "spotify", service: "Spotify", plan: null, amountCents: 1199, nextRenewal: "2026-10-20" };

function setup(props: Partial<Parameters<typeof Subscriptions>[0]> = {}) {
  const handlers = { onOpen: vi.fn(), onConnect: vi.fn() };
  render(
    <Subscriptions
      subscriptions={[NETFLIX, SPOTIFY]}
      connection={NOT_CONNECTED}
      today="2026-10-01"
      now={new Date("2026-10-01T19:00:00Z")}
      {...handlers}
      {...props}
    />,
  );
  return handlers;
}

describe("Subscriptions", () => {
  it("shows next month's total and each subscription's price and renewal", () => {
    setup();

    expect(screen.getByText(/next month/i)).toHaveTextContent("Next month: $27.48");
    expect(screen.getByRole("button", { name: /netflix/i })).toHaveTextContent("$15.49/mo");
    expect(screen.getByRole("button", { name: /spotify/i })).toHaveTextContent(/renews in 19 days/i);
  });

  it("flags trials, price increases, and cancellations", () => {
    setup({
      subscriptions: [
        { ...NETFLIX, status: "trial_ending", nextRenewal: "2026-10-05" },
        { ...SPOTIFY, previousAmountCents: 999, priceChangedAt: "2026-10-01" },
        { ...NETFLIX, serviceKey: "gym", service: "Gym", status: "cancelled", nextRenewal: null },
      ],
    });

    expect(screen.getByRole("button", { name: /netflix/i })).toHaveTextContent(/trial ends in 4 days/i);
    expect(screen.getByRole("button", { name: /spotify/i })).toHaveTextContent(/up from \$9\.99/i);
    expect(screen.getByRole("button", { name: /gym/i })).toHaveTextContent(/cancelled/i);
  });

  it("opens a subscription", async () => {
    const { onOpen } = setup();

    await userEvent.click(screen.getByRole("button", { name: /netflix/i }));

    expect(onOpen).toHaveBeenCalledWith("netflix");
  });

  it("invites you to connect Gmail when it isn't connected", async () => {
    const { onConnect } = setup({ subscriptions: [] });

    expect(screen.getByText(/no subscriptions yet/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /connect gmail/i }));
    expect(onConnect).toHaveBeenCalled();
  });

  it("shows when the Gmail script last sent something", () => {
    setup({ connection: { connected: true, createdAt: "x", lastUsedAt: "2026-10-01T18:55:00Z" } });

    expect(screen.getByText(/gmail connected · last email 5 min ago/i)).toBeInTheDocument();
  });
});
