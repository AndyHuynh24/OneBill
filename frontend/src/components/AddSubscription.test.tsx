import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type OneBillApi } from "../api";
import { fakeApi } from "../test/fakeApi";
import { AddSubscription } from "./AddSubscription";

function renderSheet(api: OneBillApi, onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AddSubscription api={api} onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

async function fill(name: string, amount: string, date: string) {
  await userEvent.type(screen.getByLabelText(/^name$/i), name);
  if (amount) await userEvent.type(screen.getByLabelText(/amount/i), amount);
  await userEvent.type(screen.getByLabelText(/next due date|next renewal/i), date);
}

describe("AddSubscription", () => {
  it("adds a bill such as rent", async () => {
    const api = fakeApi();
    const onClose = renderSheet(api);

    await userEvent.click(screen.getByRole("radio", { name: /bill/i }));
    await fill("Rent", "2100", "2026-11-01");
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));

    expect(api.addSubscription).toHaveBeenCalledWith({
      service: "Rent",
      category: "bill",
      amountCents: 210000,
      cycle: "monthly",
      nextRenewal: "2026-11-01",
    });
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("an electric bill can be added without an amount (it varies)", async () => {
    const api = fakeApi();
    renderSheet(api);

    await userEvent.click(screen.getByRole("radio", { name: /bill/i }));
    await fill("Electricity", "", "2026-10-20");
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));

    expect(api.addSubscription).toHaveBeenCalledWith(expect.not.objectContaining({ amountCents: expect.anything() }));
  });

  it("a yearly subscription", async () => {
    const api = fakeApi();
    renderSheet(api);

    await fill("Costco", "65", "2027-03-15");
    await userEvent.selectOptions(screen.getByLabelText(/how often/i), "yearly");
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));

    expect(api.addSubscription).toHaveBeenCalledWith(
      expect.objectContaining({ service: "Costco", category: "subscription", cycle: "yearly", amountCents: 6500 }),
    );
  });

  it("needs a name and a date, and a readable amount", async () => {
    const api = fakeApi();
    renderSheet(api);

    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/name/i);

    await fill("Rent", "lots", "2026-11-01");
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/amount/i);
    expect(api.addSubscription).not.toHaveBeenCalled();
  });

  it("shows the server's message, e.g. already in your list", async () => {
    const api = fakeApi({ addSubscription: vi.fn().mockRejectedValue(new ApiError("Rent is already in your list.", 409)) });
    renderSheet(api);

    await fill("Rent", "", "2026-11-01");
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already in your list/i);
  });
});
