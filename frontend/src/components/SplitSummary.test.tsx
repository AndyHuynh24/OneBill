import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Split } from "../api";
import { DINNER } from "../test/fakeApi";
import { SplitSummary } from "./SplitSummary";

const SPLIT: Split = {
  version: 1,
  totalCents: 2886,
  members: [
    { id: "m0", name: "Me", self: true, color: 0 },
    { id: "m1", name: "Bao", self: false, color: 1 },
  ],
  assignments: { "1": ["m1"], "2": ["m0"] },
  charges: { "4": { mode: "proportional" } },
  shares: [
    { member: "m0", totalCents: 1307, itemsCents: 1200, extrasCents: 107, rows: [2] },
    { member: "m1", totalCents: 1579, itemsCents: 1450, extrasCents: 129, rows: [1] },
  ],
};

afterEach(() => vi.unstubAllGlobals());

describe("SplitSummary", () => {
  it("shows what each person owes, their items, and that it adds up to the receipt", () => {
    render(<SplitSummary split={SPLIT} doc={DINNER} onEdit={vi.fn()} onDone={vi.fn()} />);

    expect(screen.getByRole("listitem", { name: /bao/i })).toHaveTextContent("$15.79");
    expect(screen.getByRole("listitem", { name: /bao/i })).toHaveTextContent("PAD THAI");
    expect(screen.getByText(/adds up to \$28\.86/i)).toBeInTheDocument();
  });

  it("Share opens the phone's share sheet with a ready-to-send message", async () => {
    const share = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, share });
    render(<SplitSummary split={SPLIT} doc={DINNER} onEdit={vi.fn()} onDone={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /share/i }));

    expect(share).toHaveBeenCalledWith({ text: expect.stringContaining("Bao $15.79 (PAD THAI + tax/tip/fees)") });
  });

  it("without a share sheet, copies the message instead", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<SplitSummary split={SPLIT} doc={DINNER} onEdit={vi.fn()} onDone={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /share/i }));

    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("THAI BASIL"));
    expect(await screen.findByText(/copied/i)).toBeInTheDocument();
  });
});
