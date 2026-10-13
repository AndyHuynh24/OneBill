import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ScanUsage } from "./ScanUsage";

const usage = { dailyLimit: 3, usedToday: 1, leftToday: 2, renewalsLeft: 2 };

describe("ScanUsage", () => {
  it("shows the daily scans and what's left today", () => {
    render(<ScanUsage usage={usage} onRenew={vi.fn()} />);

    expect(screen.getByText("3 scans a day · 2 left today")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("offers Renew once today's scans are used up", async () => {
    const onRenew = vi.fn();
    render(<ScanUsage usage={{ ...usage, usedToday: 3, leftToday: 0 }} onRenew={onRenew} />);

    await userEvent.click(screen.getByRole("button", { name: "Renew (2 left this month)" }));

    expect(onRenew).toHaveBeenCalledOnce();
  });

  it("can't renew twice while a renewal is on its way", () => {
    render(<ScanUsage usage={{ ...usage, leftToday: 0 }} onRenew={vi.fn()} renewing />);

    expect(screen.getByRole("button", { name: "Renewing…" })).toBeDisabled();
  });

  it("says when both renewals are used", () => {
    render(<ScanUsage usage={{ ...usage, leftToday: 0, renewalsLeft: 0 }} onRenew={vi.fn()} />);

    expect(screen.getByText(/none left today, more tomorrow/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
