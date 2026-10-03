import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { DocumentLine } from "../api";
import { ChargeStep } from "./ChargeStep";

const PEOPLE = [{ name: "Me", self: true }, { name: "Bao" }];
const CHARGES: DocumentLine[] = [
  { row: 6, description: "TAX", kind: "tax", amountCents: 296 },
  { row: 7, description: "20% TIP", kind: "tip", amountCents: 710 },
  { row: 8, description: "SF MANDATE", kind: "fee", amountCents: 150 },
];

function setup(onDone = vi.fn()) {
  render(<ChargeStep people={PEOPLE} lines={CHARGES} currency="USD" onDone={onDone} />);
  return onDone;
}

describe("ChargeStep", () => {
  it("shows each tax/fee/tip as a card in the middle, one at a time", () => {
    setup();

    expect(screen.getByTestId("item-card")).toHaveTextContent("TAX");
    expect(screen.getByTestId("item-card")).toHaveTextContent("$2.96");
    expect(screen.getByText("1 of 3")).toBeInTheDocument();
  });

  it("split by what each ordered, evenly, or tap (or drag to) one person who pays it all", async () => {
    const onDone = setup();

    await userEvent.click(screen.getByRole("button", { name: /by what each ordered/i }));
    await userEvent.click(screen.getByRole("button", { name: /^bao/i })); // select…
    await userEvent.click(screen.getByRole("button", { name: "Bao pays it all" })); // …and confirm
    await userEvent.click(screen.getByRole("button", { name: /evenly/i }));

    expect(onDone).toHaveBeenCalledWith({
      "6": { mode: "proportional" },
      "7": { mode: "person", member: 1 },
      "8": { mode: "even" },
    });
  });

  it("Undo goes back to the previous charge", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: /evenly/i }));

    await userEvent.click(screen.getByRole("button", { name: /undo/i }));

    expect(screen.getByTestId("item-card")).toHaveTextContent("TAX");
  });
});
