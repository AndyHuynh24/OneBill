import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { DocumentLine } from "../api";
import type { Person } from "../split";
import { AssignCircle } from "./AssignCircle";

const PEOPLE: Person[] = [{ name: "Me", self: true }, { name: "Bao" }, { name: "Linh" }, { name: "Minh" }];
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

async function giveTo(...names: string[]) {
  for (const name of names) await userEvent.click(person(name));
  await userEvent.click(screen.getByRole("button", { name: /^(give to|split among)/i }));
}

describe("AssignCircle", () => {
  it("shows one item at a time with progress; summary lines never appear", () => {
    setup();

    expect(card()).toHaveTextContent("PAD THAI");
    expect(card()).toHaveTextContent("$14.50");
    expect(screen.getByText("1 of 4")).toBeInTheDocument();
    expect(screen.queryByText("SUBTOTAL")).not.toBeInTheDocument();
  });

  it("tapping people selects them; nothing is assigned until you confirm", async () => {
    setup();

    await userEvent.click(person("Bao"));

    expect(person("Bao")).toHaveAttribute("aria-pressed", "true");
    expect(card()).toHaveTextContent("PAD THAI"); // still the same item
    expect(screen.getByRole("button", { name: "Give to Bao" })).toBeInTheDocument();
  });

  it("tapping again deselects", async () => {
    setup();

    await userEvent.click(person("Bao"));
    await userEvent.click(person("Bao"));

    expect(person("Bao")).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("button", { name: /give to/i })).not.toBeInTheDocument();
  });

  it("confirming gives the item to the selected person and moves on", async () => {
    setup();

    await giveTo("Bao");

    expect(card()).toHaveTextContent("COUPON");
    expect(screen.getByText("2 of 4")).toBeInTheDocument();
    expect(person("Bao")).toHaveAccessibleName(/\$14\.50/); // running subtotal
  });

  it("several selected people split an item between them", async () => {
    setup();

    await userEvent.click(person("Me"));
    await userEvent.click(person("Linh"));

    expect(screen.getByRole("button", { name: "Split among Me & Linh" })).toBeInTheDocument();
    await userEvent.click(person("Minh"));
    expect(screen.getByRole("button", { name: "Split among Me, Linh +1" })).toBeInTheDocument();
  });

  it("offers the item above's people for a discount", async () => {
    setup();
    await giveTo("Bao");

    await userEvent.click(screen.getByRole("button", { name: /same as pad thai/i }));

    expect(card()).toHaveTextContent("CURRY");
    expect(person("Bao")).toHaveAccessibleName(/\$12\.50/);
  });

  it("Everyone, a group, and single people: the assignments sent on", async () => {
    const onDone = setup();
    await giveTo("Bao"); // pad thai
    await giveTo("Bao"); // coupon
    await userEvent.click(screen.getByRole("button", { name: /^everyone$/i })); // curry
    await giveTo("Me", "Linh"); // rolls

    expect(onDone).toHaveBeenCalledWith({ "1": [1], "2": [1], "3": [0, 1, 2, 3], "4": [0, 2] });
  });

  it("Undo goes back one item and clears it", async () => {
    setup();
    await giveTo("Bao");

    await userEvent.click(screen.getByRole("button", { name: /undo/i }));

    expect(card()).toHaveTextContent("PAD THAI");
    expect(person("Bao")).not.toHaveAccessibleName(/\$/);
  });

  it("any item can be revisited from the list", async () => {
    const onDone = setup();
    await giveTo("Bao");
    await giveTo("Bao");
    await giveTo("Linh");

    await userEvent.click(screen.getByRole("button", { name: /PAD THAI.*Bao/ }));
    await giveTo("Me");
    await giveTo("Me"); // ROLLS, the last unassigned item

    expect(onDone).toHaveBeenCalledWith({ "1": [0], "2": [1], "3": [2], "4": [0] });
  });
});
