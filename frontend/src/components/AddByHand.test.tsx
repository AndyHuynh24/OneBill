import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { fakeApi } from "../test/fakeApi";
import { OweMe, TypeBill } from "./AddByHand";

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

describe("TypeBill", () => {
  it("adds items by hand, shows the total, and opens the saved bill", async () => {
    const api = fakeApi();
    const onSaved = vi.fn();
    wrap(<TypeBill api={api} onClose={vi.fn()} onSaved={onSaved} />);

    await userEvent.type(screen.getByLabelText(/store or what it was for/i), "Farmers market");
    await userEvent.type(screen.getByLabelText("Item 1"), "Peaches");
    await userEvent.type(screen.getByLabelText("Item 1 price"), "6");
    await userEvent.click(screen.getByRole("button", { name: /add another item/i }));
    await userEvent.type(screen.getByLabelText("Item 2"), "Honey");
    await userEvent.type(screen.getByLabelText("Item 2 price"), "12.00");
    await userEvent.type(screen.getByLabelText(/tip/i), "2");

    expect(screen.getByText("$20.00")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(api.createManualBill).toHaveBeenCalledWith(
      expect.objectContaining({
        vendor: "Farmers market",
        items: [
          { description: "Peaches", amountCents: 600 },
          { description: "Honey", amountCents: 1200 },
        ],
        tipCents: 200,
      }),
    );
    expect(onSaved).toHaveBeenCalledWith("typed-doc");
  });

  it("says what's missing instead of saving", async () => {
    const api = fakeApi();
    wrap(<TypeBill api={api} onClose={vi.fn()} onSaved={vi.fn()} />);

    await userEvent.type(screen.getByLabelText(/store or what it was for/i), "Market");
    await userEvent.type(screen.getByLabelText("Item 1"), "Peaches");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(screen.getByRole("alert")).toHaveTextContent(/price/i);
    expect(api.createManualBill).not.toHaveBeenCalled();
  });
});

describe("OweMe", () => {
  it("records who owes you, how much, and what for", async () => {
    const api = fakeApi();
    const onClose = vi.fn();
    wrap(<OweMe api={api} friends={["Minh", "Bao"]} onClose={onClose} />);

    await userEvent.click(screen.getByRole("button", { name: "Minh" }));
    await userEvent.type(screen.getByLabelText(/how much/i), "50");
    await userEvent.type(screen.getByLabelText(/what for/i), "Concert ticket");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(api.createOwed).toHaveBeenCalledWith(
      expect.objectContaining({ name: "Minh", amountCents: 5000, note: "Concert ticket" }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("takes a new name too, and needs an amount", async () => {
    const api = fakeApi();
    wrap(<OweMe api={api} friends={[]} onClose={vi.fn()} />);

    await userEvent.type(screen.getByLabelText(/who owes you/i), "Linh");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(screen.getByRole("alert")).toHaveTextContent(/how much/i);
    expect(api.createOwed).not.toHaveBeenCalled();
  });
});
