import { fireEvent, render, screen } from "@testing-library/react";
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

  it("any item can be revisited from the list", async () => {
    const onDone = setup();
    await giveTo("Bao");
    await giveTo("Bao");
    await giveTo("Linh");

    await userEvent.click(screen.getByRole("button", { name: /PAD THAI.*Bao/ }));
    expect(person("Bao")).toHaveAttribute("aria-pressed", "true"); // who has it, shown as selected
    await userEvent.click(person("Bao")); // deselect
    await giveTo("Me");
    await giveTo("Me"); // ROLLS, the last unassigned item

    expect(onDone).toHaveBeenCalledWith({ "1": [0], "2": [1], "3": [2], "4": [0] });
  });
});


describe("AssignCircle: tax, tips, and fees", () => {
  it("come after the items and can be shared by some people, or by what's ordered", async () => {
    const onDone = vi.fn();
    const lines = [
      { row: 1, description: "PAD THAI", kind: "item" as const, amountCents: 1450 },
      { row: 2, description: "TIP", kind: "tip" as const, amountCents: 300 },
    ];
    render(
      <AssignCircle
        people={[{ name: "Me", self: true }, { name: "Bao" }, { name: "Linh" }]}
        lines={lines.slice(0, 1)}
        chargeLines={lines.slice(1)}
        currency="USD"
        onDone={onDone}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /^bao/i }));
    await userEvent.click(screen.getByRole("button", { name: /^give to/i }));
    expect(screen.getByRole("button", { name: /by what's ordered/i })).toBeInTheDocument(); // the tip card
    await userEvent.click(screen.getByRole("button", { name: /^bao/i }));
    await userEvent.click(screen.getByRole("button", { name: /^linh/i }));
    await userEvent.click(screen.getByRole("button", { name: /^split among/i }));

    expect(onDone).toHaveBeenCalledWith({ "1": [1], "2": [1, 2] });
  });
});

describe("AssignCircle: the card's color border", () => {
  it("one border with a segment per chosen person", async () => {
    setup();
    await userEvent.click(person("Bao"));
    await userEvent.click(person("Linh"));

    const ring = card().style.getPropertyValue("--ring");
    expect(card()).toHaveClass("ringed");
    expect(ring).toMatch(/^conic-gradient\(/);
    expect(ring.match(/%,|%\)/g)).toHaveLength(2); // two segments
  });

  it("no border color when nobody is chosen", () => {
    setup();
    expect(card()).not.toHaveClass("ringed");
  });
});

describe("AssignCircle: dragging the card", () => {
  // jsdom has no layout: "what's under the pointer" is whichever person we say.
  function hoverOver(name: string) {
    const target = person(name);
    document.elementsFromPoint = () => [target];
  }

  it("dragging over someone and back keeps one card with one name", () => {
    setup();
    fireEvent.pointerDown(card(), { clientX: 100, clientY: 100, pointerId: 1 });

    hoverOver("Bao");
    fireEvent.pointerMove(card(), { clientX: 160, clientY: 40, pointerId: 1 });
    hoverOver("Linh");
    fireEvent.pointerMove(card(), { clientX: 40, clientY: 40, pointerId: 1 });
    fireEvent.pointerMove(card(), { clientX: 100, clientY: 100, pointerId: 1 });

    expect(screen.getAllByTestId("item-card")).toHaveLength(1);
    expect(screen.getAllByText("PAD THAI")).toHaveLength(2); // the card and the list row
  });

  it("dropping on someone gives them the item and shows them on the next card's list", () => {
    setup();
    fireEvent.pointerDown(card(), { clientX: 100, clientY: 100, pointerId: 1 });
    hoverOver("Bao");
    fireEvent.pointerMove(card(), { clientX: 160, clientY: 40, pointerId: 1 });
    fireEvent.pointerUp(card(), { clientX: 160, clientY: 40, pointerId: 1 });

    expect(card()).toHaveTextContent("COUPON");
    expect(screen.getAllByText("COUPON")).toHaveLength(2);
    expect(person("Bao")).toHaveAccessibleName(/\$14\.50/);
  });

  it("an assigned item shows who has it as selected people and a colored border", async () => {
    setup();
    await giveTo("Me", "Linh");
    await userEvent.click(screen.getByRole("button", { name: /^PAD THAI/ }));

    expect(person("Me")).toHaveAttribute("aria-pressed", "true");
    expect(person("Linh")).toHaveAttribute("aria-pressed", "true");
    expect(person("Bao")).toHaveAttribute("aria-pressed", "false");
    expect(card()).toHaveClass("ringed");
    expect(screen.queryByLabelText(/assigned to/i)).not.toBeInTheDocument(); // no small avatars
  });

  it("a new item starts with nobody selected", async () => {
    setup();
    await giveTo("Bao");

    expect(card()).toHaveTextContent("COUPON");
    expect(person("Bao")).toHaveAttribute("aria-pressed", "false");
    expect(card()).not.toHaveClass("ringed");
  });
});

describe("AssignCircle: 7 or more people", () => {
  const MANY: Person[] = ["Me", "Bao", "Linh", "Minh", "Christopher", "Anh", "Tuan"].map((name, i) => ({
    name,
    self: i === 0,
  }));

  it("uses a grid, cuts long names, and still assigns by tapping", async () => {
    const onDone = vi.fn();
    render(<AssignCircle people={MANY} lines={LINES.slice(0, 1)} currency="USD" onDone={onDone} />);

    expect(document.querySelector(".people-grid")).toBeInTheDocument();
    expect(screen.getByText("Christoph…")).toBeInTheDocument();
    expect(person("Christopher")).toBeInTheDocument(); // full name for screen readers

    await userEvent.click(person("Christopher"));
    await userEvent.click(person("Tuan"));
    await userEvent.click(screen.getByRole("button", { name: /^split among/i }));
    await userEvent.click(screen.getByRole("button", { name: /^finish split/i }));

    expect(onDone).toHaveBeenCalledWith({ "1": [4, 6] });
  });
});
