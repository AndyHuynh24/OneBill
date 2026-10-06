import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { DocumentLine } from "../api";
import { SplitList } from "./SplitList";

const PEOPLE = [{ name: "Me", self: true }, { name: "Bao" }, { name: "Linh" }];
const LINES: DocumentLine[] = [
  { row: 1, description: "PAD THAI", kind: "item", amountCents: 1450 },
  { row: 2, description: "COUPON", kind: "discount", amountCents: -200 },
  { row: 3, description: "CURRY", kind: "item", amountCents: 1200 },
];
const TAX: DocumentLine[] = [{ row: 6, description: "TAX", kind: "tax", amountCents: 296 }];

function setup() {
  const onDone = vi.fn();
  render(<SplitList people={PEOPLE} lines={LINES} chargeLines={TAX} currency="USD" onDone={onDone} />);
  return onDone;
}
const line = (name: RegExp) => screen.getByRole("button", { name });
const tap = (name: RegExp) => userEvent.click(line(name));
const finish = () => screen.getByRole("button", { name: /finish split/i });
const banner = () => screen.getByRole("status");

describe("SplitList", () => {
  it("starts with you selected: lines you tap are yours; Next hands over to the next person", async () => {
    const onDone = setup();
    expect(banner()).toHaveTextContent(/tap what you had/i);

    await tap(/^PAD THAI/); // Me
    await userEvent.click(within(banner()).getByRole("button", { name: /next: bao/i }));
    await tap(/^PAD THAI/); // Bao shares it
    await tap(/^CURRY/);
    await userEvent.click(within(banner()).getByRole("button", { name: /next: linh/i }));
    await tap(/^COUPON/); // a discount isn't given away automatically
    await userEvent.click(within(banner()).getByRole("button", { name: /^done$/i })); // last person
    await userEvent.click(finish());

    expect(onDone).toHaveBeenCalledWith({ "1": [0, 1], "2": [2], "3": [1], "6": "byOrder" });
  });

  it("with nobody selected, tapping a line opens the picker", async () => {
    const onDone = setup();
    // Stop selecting: tap the highlighted person.
    await userEvent.click(within(screen.getByRole("group", { name: "People" })).getByRole("button", { pressed: true }));

    for (const name of [/^PAD THAI/, /^COUPON/, /^CURRY/]) {
      await tap(name);
      const picker = screen.getByRole("dialog", { name: /who had/i });
      await userEvent.click(within(picker).getByRole("button", { name: /^everyone$/i }));
      await userEvent.click(within(picker).getByRole("button", { name: /^done$/i }));
    }
    await userEvent.click(finish());

    expect(onDone).toHaveBeenCalledWith({ "1": [0, 1, 2], "2": [0, 1, 2], "3": [0, 1, 2], "6": "byOrder" });
  });

  it("tax always opens its picker, even while someone is selected; split the rest evenly", async () => {
    const onDone = setup();

    await userEvent.click(screen.getByRole("button", { name: /split the rest evenly/i }));
    await tap(/^TAX/);
    const picker = screen.getByRole("dialog", { name: /who had tax/i });
    // By what's ordered shows everyone selected, each with their share; leave only Me.
    expect(within(picker).getByRole("button", { name: /^me/i })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(within(picker).getByRole("button", { name: /^bao/i }));
    await userEvent.click(within(picker).getByRole("button", { name: /^linh/i }));
    await userEvent.click(within(picker).getByRole("button", { name: /^done$/i }));
    await userEvent.click(finish());

    expect(onDone).toHaveBeenCalledWith({ "1": [0, 1, 2], "2": [0, 1, 2], "3": [0, 1, 2], "6": [0] });
  });
});
