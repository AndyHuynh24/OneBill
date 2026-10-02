import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Obligation } from "../api";
import { Upcoming } from "./Upcoming";

const ITEMS: Obligation[] = [
  { oblId: "o1", docId: "d1", kind: "return_by", dueDate: "2026-10-20", vendor: "Walmart", amountCents: 2618 },
  { oblId: "o2", docId: "", kind: "renewal", dueDate: "2026-11-01", vendor: "Netflix", amountCents: 1549 },
];

describe("Upcoming", () => {
  it("removes a reminder after a quick confirm", async () => {
    const onRemove = vi.fn();
    render(<Upcoming obligations={ITEMS} onRemove={onRemove} />);

    await userEvent.click(screen.getByRole("button", { name: /remove reminder: return by walmart/i }));
    expect(onRemove).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /^remove$/i }));

    expect(onRemove).toHaveBeenCalledWith(ITEMS[0]);
  });

  it("keeps it if you change your mind", async () => {
    const onRemove = vi.fn();
    render(<Upcoming obligations={ITEMS} onRemove={onRemove} />);

    await userEvent.click(screen.getByRole("button", { name: /remove reminder: renews netflix/i }));
    await userEvent.click(screen.getByRole("button", { name: /keep/i }));

    expect(onRemove).not.toHaveBeenCalled();
    expect(screen.getByText(/netflix/i)).toBeInTheDocument();
  });

  it("explains that removing a renewal stops that subscription's reminders", async () => {
    render(<Upcoming obligations={ITEMS} onRemove={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /remove reminder: renews netflix/i }));

    expect(screen.getByText(/stops reminders for netflix/i)).toBeInTheDocument();
  });
});
