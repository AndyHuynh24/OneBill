import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Friend } from "../api";
import type { Person } from "../split";
import { PeoplePicker } from "./PeoplePicker";

const FRIENDS: Friend[] = [
  { friendId: "f1", name: "Bao", color: 3, timesUsed: 5, lastUsedAt: "x" },
  { friendId: "f2", name: "Linh", color: 4, timesUsed: 2, lastUsedAt: "x" },
];

function Harness({ onNext = vi.fn(), onForget = vi.fn() }) {
  const [people, setPeople] = useState<Person[]>([{ name: "Me", self: true }]);
  return (
    <PeoplePicker
      friends={FRIENDS}
      people={people}
      onChange={setPeople}
      onNext={onNext}
      onForget={onForget}
    />
  );
}

const seats = () => within(screen.getByRole("list", { name: /in this split/i })).getAllByRole("listitem");

describe("PeoplePicker", () => {
  it("starts with Me, and Next needs at least one more person", () => {
    render(<Harness />);

    expect(seats().map((s) => s.textContent)).toEqual([expect.stringContaining("Me")]);
    expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
  });

  it("adds and removes recent friends with one tap", async () => {
    render(<Harness />);

    await userEvent.click(screen.getByRole("button", { name: "Bao" }));
    expect(screen.getByRole("button", { name: "Bao" })).toHaveAttribute("aria-pressed", "true");
    expect(seats()).toHaveLength(2);

    await userEvent.click(screen.getByRole("button", { name: "Bao" }));
    expect(seats()).toHaveLength(1);
  });

  it("adds several new names from one phrase, in seat order", async () => {
    render(<Harness />);

    await userEvent.type(screen.getByLabelText(/add names/i), "Tam and Vy, bao{Enter}");

    expect(seats().map((s) => s.textContent)).toEqual([
      expect.stringContaining("Me"),
      expect.stringContaining("Tam"),
      expect.stringContaining("Vy"),
      expect.stringContaining("bao"),
    ]);
    expect(screen.getByLabelText(/add names/i)).toHaveValue("");
    expect(screen.getByRole("button", { name: "Bao" })).toHaveAttribute("aria-pressed", "true");
  });

  it("removes someone from the split, but never Me", async () => {
    render(<Harness />);
    await userEvent.type(screen.getByLabelText(/add names/i), "Tam{Enter}");

    await userEvent.click(screen.getByRole("button", { name: "Remove Tam" }));

    expect(seats()).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Remove Me" })).not.toBeInTheDocument();
  });

  it("caps the group at 12 people", async () => {
    render(<Harness />);

    await userEvent.type(
      screen.getByLabelText(/add names/i),
      "A, B, C, D, E, F, G, H, I, J, K, L, M, N{Enter}",
    );

    expect(seats()).toHaveLength(12);
    expect(screen.getByRole("status")).toHaveTextContent(/12 people/i);
  });

  it("passes the people to Next", async () => {
    const onNext = vi.fn();
    render(<Harness onNext={onNext} />);
    await userEvent.click(screen.getByRole("button", { name: "Linh" }));

    await userEvent.click(screen.getByRole("button", { name: /next/i }));

    expect(onNext).toHaveBeenCalledWith([{ name: "Me", self: true }, { name: "Linh" }]);
  });

  it("Edit lets you forget a recent friend", async () => {
    const onForget = vi.fn();
    render(<Harness onForget={onForget} />);

    await userEvent.click(screen.getByRole("button", { name: /edit/i }));
    await userEvent.click(screen.getByRole("button", { name: "Forget Bao" }));

    expect(onForget).toHaveBeenCalledWith(FRIENDS[0]);
  });
});
