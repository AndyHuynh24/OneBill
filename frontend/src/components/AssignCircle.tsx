import { useState } from "react";
import type { DocumentLine } from "../api";
import { formatCents } from "../format";
import { assignLabel, type Person } from "../split";
import { useCardQueue } from "../useCardQueue";
import { type Flight, PeopleCircle } from "./PeopleCircle";

/** Seat indexes per row number (JSON-style keys, as the API expects). */
export type Assignments = Record<string, number[]>;

interface AssignCircleProps {
  people: Person[];
  lines: DocumentLine[];
  currency: string;
  initial?: Assignments;
  onDone: (assignments: Assignments) => void;
  /** Go to tax & tip with the current assignments (when the bill has any). */
  onCharges?: (assignments: Assignments) => void;
}

const CARD_KINDS = new Set(["item", "discount"]);

/**
 * Items one at a time in the middle of the circle. Drag an item onto someone, or tap one or
 * more people and confirm ("Give to Bao" / "Split among Bao & Linh"). Everyone, Undo, and a
 * list to revisit any item.
 */
export function AssignCircle({
  people,
  lines,
  currency,
  initial = {},
  onDone,
  onCharges,
}: AssignCircleProps) {
  const cards = lines.filter((l) => CARD_KINDS.has(l.kind));
  const queue = useCardQueue<number[]>(
    cards.map((l) => String(l.row)),
    initial,
    onDone,
  );
  const [selected, setSelected] = useState<number[]>([]);
  const [flight, setFlight] = useState<Flight | null>(null);
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
    const sorted = [...seats].sort((a, b) => a - b);
    setFlight((f) => ({ seats: sorted, id: (f?.id ?? 0) + 1 }));
    setSelected([]);
    queue.choose(sorted);
  }

  const toggle = (seat: number) =>
    setSelected((current) =>
      current.includes(seat)
        ? current.filter((s) => s !== seat)
        : [...current, seat],
    );

  // A discount usually belongs to whoever got the item printed just above it.
  const above =
    card?.kind === "discount"
      ? [...cards]
          .reverse()
          .find(
            (l) =>
              l.kind === "item" && l.row < card.row && assigned[String(l.row)],
          )
      : undefined;
  const nameOf = (seats: number[] | undefined) =>
    (seats ?? []).map((s) => people[s]?.name).join(", ");

  return (
    <div className="assign">
      <div className="progress" aria-hidden="true">
        <span
          style={{
            width: `${cards.length ? (done / cards.length) * 100 : 0}%`,
          }}
        />
      </div>
      <p className="muted assign-progress">
        {Math.min(done + 1, cards.length)} of {cards.length}
      </p>

      <PeopleCircle
        people={people}
        currency={currency}
        amounts={subtotals}
        selected={selected}
        onSeat={toggle}
        onDrop={(seat) => assign([seat])}
        flight={flight}
        card={
          card && (
            <div key={card.row} className="item-card-content">
              {card.kind === "discount" && (
                <span className="item-kind">Discount</span>
              )}
              <span className="item-name">{card.description}</span>
              <strong>{formatCents(card.amountCents, currency)}</strong>
            </div>
          )
        }
      />

      <div className="assign-cta">
        {selected.length > 0 ? (
          <button
            type="button"
            className="primary"
            onClick={() => assign(selected)}
          >
            {assignLabel(
              [...selected].sort((a, b) => a - b).map((s) => people[s].name),
            )}
          </button>
        ) : (
          <p className="muted assign-hint">
            Drag the item to someone, or tap people to choose.
          </p>
        )}
      </div>

      <div className="assign-actions">
        {above && (
          <button
            type="button"
            onClick={() => assign(assigned[String(above.row)])}
          >
            Same as {above.description} ({nameOf(assigned[String(above.row)])})
          </button>
        )}
        <button type="button" onClick={() => assign(people.map((_, i) => i))}>
          Everyone
        </button>
        <button
          type="button"
          disabled={!queue.canUndo}
          onClick={() => queue.undo()}
        >
          Undo
        </button>
      </div>

      {cards.length > 1 && (
        <div className="item-nav">
          <button
            type="button"
            disabled={queue.current === 0}
            onClick={() => {
              setSelected([]);
              queue.jump(queue.current - 1);
            }}
          >
            ‹ Previous
          </button>
          <button
            type="button"
            disabled={queue.current >= cards.length - 1}
            onClick={() => {
              setSelected([]);
              queue.jump(queue.current + 1);
            }}
          >
            Next ›
          </button>
        </div>
      )}

      {queue.complete && (
        <div className="finish-actions">
          <button type="button" className="btn-block btn-split" onClick={queue.finish}>
            Finish split
          </button>
          {onCharges && (
            <button type="button" className="link-button" onClick={() => onCharges(queue.values)}>
              Tax &amp; tip
            </button>
          )}
        </div>
      )}

      <ul className="assign-list" aria-label="Items">
        {cards.map((l, i) => (
          <li key={l.row}>
            <button
              type="button"
              className={
                i === queue.current ? "assign-row current" : "assign-row"
              }
              onClick={() => {
                setSelected([]);
                queue.jump(i);
              }}
            >
              <span>{l.description}</span>
              <span className="muted">
                {" "}
                — {nameOf(assigned[String(l.row)]) || "not assigned"}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
