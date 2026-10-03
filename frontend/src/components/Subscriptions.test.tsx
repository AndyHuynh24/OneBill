import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Subscription } from "../api";
import { NETFLIX } from "../test/fakeApi";
import { Subscriptions } from "./Subscriptions";

const NOT_CONNECTED = { connected: false, createdAt: null, lastUsedAt: null };
const SPOTIFY = { ...NETFLIX, serviceKey: "spotify", service: "Spotify", plan: null, amountCents: 1199, nextRenewal: "2026-10-20" };
const ICLOUD = { ...NETFLIX, serviceKey: "icloud", service: "iCloud+", amountCents: 299, nextRenewal: "2026-10-08" };
const COSTCO = { ...NETFLIX, serviceKey: "costco", service: "Costco", cycle: "yearly" as const, amountCents: 6500, nextRenewal: "2027-03-15" };
const RENT: Subscription = { ...NETFLIX, serviceKey: "rent", service: "Rent", category: "bill", amountCents: 210000, nextRenewal: "2026-10-05" };

function setup(props: Partial<Parameters<typeof Subscriptions>[0]> = {}) {
  const handlers = { onOpen: vi.fn(), onConnect: vi.fn(), onAdd: vi.fn(), onViewAll: vi.fn() };
  render(
    <Subscriptions
      subscriptions={[NETFLIX, SPOTIFY, ICLOUD, COSTCO]}
      connection={NOT_CONNECTED}
      today="2026-10-01"
      now={new Date("2026-10-01T19:00:00Z")}
      limit={3}
      {...handlers}
      {...props}
    />,
  );
  return handlers;
}

const stat = (label: RegExp) => screen.getByText(label).closest(".stat") as HTMLElement;
const rows = () => within(screen.getByRole("table", { name: /subscriptions/i })).getAllByRole("row");

describe("Subscriptions summary", () => {
  it("shows how many are active, next month's total, and what renews in the next 30 days", () => {
    setup();

    expect(stat(/^active$/i)).toHaveTextContent("4");
    expect(stat(/next month/i)).toHaveTextContent("$30.47"); // 15.49 + 11.99 + 2.99
    expect(stat(/next 30 days/i)).toHaveTextContent("$14.98"); // Spotify Oct 20 + iCloud Oct 8
  });

  it("lists only the soonest few, each with how often it's billed", () => {
    setup();

    expect(rows()).toHaveLength(3);
    expect(rows()[0]).toHaveTextContent(/iCloud\+.*Monthly.*\$2\.99.*in 7 days/);
    expect(rows()[1]).toHaveTextContent(/Spotify.*Monthly/);
    expect(screen.queryByText("Costco")).not.toBeInTheDocument(); // renews in March
  });

  it("View all opens the full list", async () => {
    const { onViewAll } = setup();

    await userEvent.click(screen.getByRole("button", { name: /view all/i }));

    expect(onViewAll).toHaveBeenCalled();
  });

  it("the full list (no limit) shows everything, yearly ones included", () => {
    setup({ limit: undefined, onViewAll: undefined });

    expect(rows()).toHaveLength(4);
    expect(screen.getByText("Costco").closest("tr")).toHaveTextContent(/Yearly.*\$65\.00/);
    expect(screen.queryByRole("button", { name: /view all/i })).not.toBeInTheDocument();
  });

  it("bills say when they're due; trials and price rises are flagged", () => {
    setup({
      subscriptions: [RENT, { ...NETFLIX, status: "trial_ending", nextRenewal: "2026-10-05" }, { ...SPOTIFY, previousAmountCents: 999 }],
    });

    expect(screen.getByText("Rent").closest("tr")).toHaveTextContent(/due in 4 days/i);
    expect(screen.getByText("Netflix").closest("tr")).toHaveTextContent(/trial ends in 4 days/i);
    expect(screen.getByText("Spotify").closest("tr")).toHaveTextContent(/up from \$9\.99/i);
  });

  it("opens one", async () => {
    const { onOpen } = setup();

    await userEvent.click(screen.getByRole("button", { name: /spotify/i }));

    expect(onOpen).toHaveBeenCalledWith("spotify");
  });

  it("+ Add, and Connect Gmail when it isn't connected", async () => {
    const { onAdd, onConnect } = setup({ subscriptions: [] });

    expect(screen.getByText(/no subscriptions yet/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /add/i }));
    await userEvent.click(screen.getByRole("button", { name: /connect gmail/i }));
    expect(onAdd).toHaveBeenCalled();
    expect(onConnect).toHaveBeenCalled();
  });

  it("shows when the Gmail script last sent something", () => {
    setup({ connection: { connected: true, createdAt: "x", lastUsedAt: "2026-10-01T18:55:00Z" } });

    expect(screen.getByText(/gmail connected · last email 5 min ago/i)).toBeInTheDocument();
  });
});
