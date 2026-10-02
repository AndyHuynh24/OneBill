import { useState } from "react";
import type { DocumentLine } from "../api";
import { formatCents } from "../format";
import type { Person } from "../split";
import { useCardQueue } from "../useCardQueue";
import { PeopleCircle } from "./PeopleCircle";

/** Seat indexes per row number (JSON-style keys, as the API expects). */
export type Assignments = Record<string, number[]>;

interface AssignCircleProps {
  people: Person[];
  lines: DocumentLine[];
  currency: string;
  initial?: Assignments;
  onDone: (assignments: Assignments) => void;
}

const CARD_KINDS = new Set(["item", "discount"]);

/** Items one at a time in the middle of the circle: tap or drag to a person, Everyone, or Share. */
export function AssignCircle({ people, lines, currency, initial = {}, onDone }: AssignCircleProps) {
  const cards = lines.filter((l) => CARD_KINDS.has(l.kind));
  const queue = useCardQueue<number[]>(
    cards.map((l) => String(l.row)),
    initial,
    onDone,
  );
  const [sharing, setSharing] = useState<number[] | null>(null);
  const card = cards[queue.current];
  const assigned = queue.values;

  const subtotals = people.map((_, seat) =>
    cards.reduce((sum, l) => {
      const seats = assigned[String(l.row)] ?? [];
      return seats.includes(seat) ? sum + l.amountCents / seats.length : sum;
    }, 0),
  );
  const done = cards.filter((l) => assigned[String(l.row)]).length;

  function assign(seats: number[]) {
    if (!seats.length) return;
    setSharing(null);
    queue.choose([...seats].sort((a, b) => a - b));
  }

  function tapSeat(seat: number) {
    if (sharing) setSharing(sharing.includes(seat) ? sharing.filter((s) => s !== seat) : [...sharing, seat]);
    else assign([seat]);
  }

  // A discount usually belongs to whoever got the item printed just above it.
  const above =
    card?.kind === "discount"
      ? [...cards].reverse().find((l) => l.kind === "item" && l.row < card.row && assigned[String(l.row)])
      : undefined;
  const nameOf = (seats: number[] | undefined) => (seats ?? []).map((s) => people[s]?.name).join(", ");

  return (
    <div className="assign">
      <p className="muted assign-progress">
        {Math.min(done + 1, cards.length)} of {cards.length}
      </p>
      <PeopleCircle
        people={people}
        currency={currency}
        amounts={subtotals}
        selected={sharing}
        onSeat={tapSeat}
        card={
          card && (
            <>
              <span className="item-name">{card.description}</span>
              <strong>{formatCents(card.amountCents, currency)}</strong>
            </>
          )
        }
      />

      <div className="assign-actions">
        {sharing ? (
          <>
            <button type="button" className="primary" disabled={!sharing.length} onClick={() => assign(sharing)}>
              Split between {sharing.length}
            </button>
            <button type="button" onClick={() => setSharing(null)}>
              Cancel
            </button>
          </>
        ) : (
          <>
            {above && (
              <button type="button" className="primary" onClick={() => assign(assigned[String(above.row)])}>
                Same as {above.description} ({nameOf(assigned[String(above.row)])})
              </button>
            )}
            <button type="button" onClick={() => assign(people.map((_, i) => i))}>
              Everyone
            </button>
            <button type="button" onClick={() => setSharing([])}>
              Share
            </button>
            <button type="button" disabled={!queue.canUndo} onClick={() => queue.undo()}>
              Undo
            </button>
            {queue.complete && (
              <button type="button" className="primary" onClick={queue.finish}>
                Next
              </button>
            )}
          </>
        )}
      </div>

      <ul className="assign-list" aria-label="Items">
        {cards.map((l, i) => (
          <li key={l.row}>
            <button
              type="button"
              className={i === queue.current ? "assign-row current" : "assign-row"}
              onClick={() => {
                setSharing(null);
                queue.jump(i);
              }}
            >
              <span>{l.description}</span>
              <span className="muted"> — {nameOf(assigned[String(l.row)]) || "not assigned"}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
