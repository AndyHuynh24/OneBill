import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { OneBillApi } from "../api";
import { NETFLIX, fakeApi } from "../test/fakeApi";
import { SubscriptionSheet } from "./SubscriptionSheet";

function renderSheet(api: OneBillApi, subscription = NETFLIX, onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SubscriptionSheet api={api} subscription={subscription} onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

describe("SubscriptionSheet", () => {
  it("shows the plan, price, renewal, and charge history", () => {
    renderSheet(fakeApi(), { ...NETFLIX, renewalComputed: true });

    expect(screen.getByRole("heading", { name: "Netflix" })).toBeInTheDocument();
    expect(screen.getByText(/standard/i)).toBeInTheDocument();
    expect(screen.getByText(/\$15\.49 · Monthly/)).toBeInTheDocument();
    expect(screen.getByText(/nov 1, 2026/i)).toHaveTextContent(/estimated/i);
    expect(screen.getByRole("table", { name: /charges/i })).toHaveTextContent("$15.49");
  });

  it("cancelling asks first, then stops reminders", async () => {
    const api = fakeApi();
    const onClose = renderSheet(api);

    await userEvent.click(screen.getByRole("button", { name: /mark cancelled/i }));
    expect(api.cancelSubscription).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /yes, stop reminders/i }));

    expect(api.cancelSubscription).toHaveBeenCalledWith("netflix");
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("Fix sends only what changed", async () => {
    const api = fakeApi();
    renderSheet(api);

    await userEvent.click(screen.getByRole("button", { name: /^fix$/i }));
    const price = screen.getByLabelText(/price/i);
    await userEvent.clear(price);
    await userEvent.type(price, "17.99");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    expect(api.editSubscription).toHaveBeenCalledWith("netflix", { amountCents: 1799 });
  });

  it("turns renewal reminders off and on", async () => {
    const api = fakeApi();
    renderSheet(api);

    await userEvent.click(screen.getByRole("checkbox", { name: /remind me before renewals/i }));

    expect(api.setSubscriptionReminders).toHaveBeenCalledWith("netflix", false);
  });

  it("Delete removes the subscription after a confirm", async () => {
    const api = fakeApi();
    const onClose = renderSheet(api);

    await userEvent.click(screen.getByRole("button", { name: /delete subscription/i }));
    expect(screen.getByText(/can come back if new netflix emails arrive/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /yes, delete/i }));

    expect(api.deleteSubscription).toHaveBeenCalledWith("netflix");
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
