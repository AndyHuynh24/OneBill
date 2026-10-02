import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { DocumentLine } from "../api";
import type { Person } from "../split";
import { AssignCircle } from "./AssignCircle";

const PEOPLE: Person[] = [{ name: "Me", self: true }, { name: "Bao" }, { name: "Linh" }];
const LINES: DocumentLine[] = [
  { row: 1, description: "PAD THAI", kind: "item", amountCents: 1450 },
  { row: 2, description: "COUPON", kind: "discount", amountCents: -200 },
  { row: 3, description: "CURRY", kind: "item", amountCents: 1200 },
  { row: 4, description: "ROLLS", kind: "item", amountCents: 900 },
  { row: 5, description: "SUBTOTAL", kind: "subtotal", amountCents: 3350 },
  { row: 6, description: "TAX", kind: "tax", amountCents: 296 },
];

function setup(onDone = vi.fn()) {
  render(<AssignCircle people={PEOPLE} lines={LINES} currency="USD" onDone={onDone} />);
  return onDone;
}

const card = () => screen.getByTestId("item-card");
const person = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name}\\b`) });

describe("AssignCircle", () => {
  it("shows one item at a time with progress; summary lines never appear", () => {
    setup();

    expect(card()).toHaveTextContent("PAD THAI");
    expect(card()).toHaveTextContent("$14.50");
    expect(screen.getByText("1 of 4")).toBeInTheDocument();
    expect(screen.queryByText("SUBTOTAL")).not.toBeInTheDocument();
  });

  it("tapping a person assigns the item and moves to the next", async () => {
    setup();

    await userEvent.click(person("Bao"));

    expect(card()).toHaveTextContent("COUPON");
    expect(screen.getByText("2 of 4")).toBeInTheDocument();
    expect(person("Bao")).toHaveAccessibleName(/\$14\.50/); // running subtotal
  });

  it("offers the item above's people for a discount", async () => {
    setup();
    await userEvent.click(person("Bao"));

    await userEvent.click(screen.getByRole("button", { name: /same as pad thai/i }));

    expect(card()).toHaveTextContent("CURRY");
    expect(person("Bao")).toHaveAccessibleName(/\$12\.50/);
  });

  it("Everyone and Share split an item between people", async () => {
    const onDone = setup();
    await userEvent.click(person("Bao")); // pad thai
    await userEvent.click(person("Bao")); // coupon
    await userEvent.click(screen.getByRole("button", { name: /^everyone$/i })); // curry

    await userEvent.click(screen.getByRole("button", { name: /^share$/i })); // rolls
    await userEvent.click(person("Me"));
    await userEvent.click(person("Linh"));
    await userEvent.click(screen.getByRole("button", { name: /split between 2/i }));

    expect(onDone).toHaveBeenCalledWith({ "1": [1], "2": [1], "3": [0, 1, 2], "4": [0, 2] });
  });

  it("Undo goes back one item and clears it", async () => {
    setup();
    await userEvent.click(person("Bao"));

    await userEvent.click(screen.getByRole("button", { name: /undo/i }));

    expect(card()).toHaveTextContent("PAD THAI");
    expect(person("Bao")).not.toHaveAccessibleName(/\$/);
  });

  it("any item can be revisited from the list", async () => {
    const onDone = setup();
    await userEvent.click(person("Bao"));
    await userEvent.click(person("Bao"));
    await userEvent.click(person("Linh"));

    await userEvent.click(screen.getByRole("button", { name: /PAD THAI.*Bao/ }));
    await userEvent.click(person("Me"));
    await userEvent.click(person("Me")); // ROLLS, the last unassigned item

    expect(onDone).toHaveBeenCalledWith({ "1": [0], "2": [1], "3": [2], "4": [0] });
  });
});
