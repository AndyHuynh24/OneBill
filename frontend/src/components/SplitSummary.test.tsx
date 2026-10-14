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

  it("'Add more people and split again' goes back to choosing people", async () => {
    const onAddPeople = vi.fn();
    render(<SplitSummary split={SPLIT} doc={DINNER} onEdit={vi.fn()} onDone={vi.fn()} onAddPeople={onAddPeople} />);

    await userEvent.click(screen.getByRole("button", { name: /add more people and split again/i }));

    expect(onAddPeople).toHaveBeenCalledOnce();
  });

  it("Share opens the phone's share sheet with a ready-to-send message", async () => {
    const share = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, share });
    render(<SplitSummary split={SPLIT} doc={DINNER} onEdit={vi.fn()} onDone={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /share/i }));

    expect(share).toHaveBeenCalledWith({ text: expect.stringContaining("Bao: $15.79\n- PAD THAI") });
  });

  it("without a share sheet, copies the message instead", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<SplitSummary split={SPLIT} doc={DINNER} onEdit={vi.fn()} onDone={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /share/i }));

    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("THAI BASIL"));
    expect(await screen.findByText(/copied/i)).toBeInTheDocument();
  });

  it("lets you tick each other person as paid, never yourself", async () => {
    const onTogglePaid = vi.fn();
    render(<SplitSummary split={SPLIT} doc={DINNER} onEdit={vi.fn()} onDone={vi.fn()} onTogglePaid={onTogglePaid} />);

    expect(screen.queryByRole("button", { name: /mark me as paid/i })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Mark Bao as paid" }));

    expect(onTogglePaid).toHaveBeenCalledWith("m1", true);
  });

  it("shows who has paid and how much is paid back", () => {
    const three: Split = {
      ...SPLIT,
      members: [...SPLIT.members, { id: "m2", name: "Linh", self: false, color: 2 }],
      shares: [...SPLIT.shares, { member: "m2", totalCents: 2105, itemsCents: 2000, extrasCents: 105, rows: [] }],
      paid: { m1: "2026-10-14T19:00:00Z" },
    };
    render(<SplitSummary split={three} doc={DINNER} onEdit={vi.fn()} onDone={vi.fn()} onTogglePaid={vi.fn()} />);

    expect(screen.getByRole("listitem", { name: /bao/i })).toHaveTextContent("Paid");
    expect(screen.getByRole("button", { name: "Mark Bao as not paid" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("listitem", { name: /linh/i })).not.toHaveTextContent("Paid");
    expect(screen.getByText("$15.79 of $36.84 paid back")).toBeInTheDocument();
  });

  it("says so when everyone has paid", () => {
    render(
      <SplitSummary
        split={{ ...SPLIT, paid: { m1: "2026-10-14T19:00:00Z" } }}
        doc={DINNER}
        onEdit={vi.fn()}
        onDone={vi.fn()}
        onTogglePaid={vi.fn()}
      />,
    );

    expect(screen.getByText(/everyone has paid/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /remind/i })).not.toBeInTheDocument();
  });

  it("Remind shares just that person's items and amount", async () => {
    const share = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, share });
    render(<SplitSummary split={SPLIT} doc={DINNER} onEdit={vi.fn()} onDone={vi.fn()} onTogglePaid={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Remind Bao" }));

    const text: string = share.mock.calls[0][0].text;
    expect(text).toContain("Bao: $15.79\n- PAD THAI");
    expect(text).not.toContain("Me:");
  });

  it("Share leaves out people who have paid", async () => {
    const share = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, share });
    render(
      <SplitSummary split={{ ...SPLIT, paid: { m1: "x" } }} doc={DINNER} onEdit={vi.fn()} onDone={vi.fn()} />,
    );

    await userEvent.click(screen.getByRole("button", { name: /^share$/i }));

    expect(share.mock.calls[0][0].text).not.toContain("Bao:");
  });
});
