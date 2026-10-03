import { useState } from "react";
import type { ChargeChoice, DocumentLine } from "../api";
import { formatCents } from "../format";
import type { Person } from "../split";
import { useCardQueue } from "../useCardQueue";
import { type Flight, PeopleCircle } from "./PeopleCircle";

export type Charges = Record<string, ChargeChoice>;

interface ChargeStepProps {
  people: Person[];
  lines: DocumentLine[];
  currency: string;
  initial?: Charges;
  onDone: (charges: Charges) => void;
}

const LABELS: Record<string, string> = { tax: "Tax", fee: "Fee", tip: "Tip" };

/** Tax, fees, and tip as cards in the middle: split by what each ordered, evenly, or one person. */
export function ChargeStep({ people, lines, currency, initial = {}, onDone }: ChargeStepProps) {
  const queue = useCardQueue<ChargeChoice>(
    lines.map((l) => String(l.row)),
    initial,
    onDone,
  );
  const card = lines[queue.current];
  const chosen = queue.key ? queue.values[queue.key] : undefined;
  const [selected, setSelected] = useState<number | null>(null);
  const [flight, setFlight] = useState<Flight | null>(null);

  function choose(choice: ChargeChoice) {
    const seats = choice.mode === "person" ? [choice.member] : people.map((_, i) => i);
    setFlight((f) => ({ seats, id: (f?.id ?? 0) + 1 }));
    setSelected(null);
    queue.choose(choice);
  }

  return (
    <div className="assign">
      <div className="progress" aria-hidden="true">
        <span style={{ width: `${lines.length ? (queue.current / lines.length) * 100 : 0}%` }} />
      </div>
      <p className="muted assign-progress">
        {queue.current + 1} of {lines.length}
      </p>
      <PeopleCircle
        people={people}
        currency={currency}
        selected={selected !== null ? [selected] : chosen?.mode === "person" ? [chosen.member] : []}
        onSeat={(seat) => setSelected((current) => (current === seat ? null : seat))}
        onDrop={(seat) => choose({ mode: "person", member: seat })}
        flight={flight}
        card={
          card && (
            <div key={card.row} className="item-card-content">
              <span className="item-kind">{LABELS[card.kind]}</span>
              <span className="item-name">{card.description}</span>
              <strong>{formatCents(card.amountCents, currency)}</strong>
            </div>
          )
        }
      />
      <div className="assign-cta">
        {selected !== null ? (
          <button type="button" className="primary" onClick={() => choose({ mode: "person", member: selected })}>
            {people[selected].name} pays it all
          </button>
        ) : (
          <p className="muted assign-hint">Split it below, or tap (or drag to) the one person paying it all.</p>
        )}
      </div>
      <div className="assign-actions">
        <button
          type="button"
          className="primary"
          aria-pressed={chosen?.mode === "proportional"}
          onClick={() => choose({ mode: "proportional" })}
        >
          By what each ordered
        </button>
        <button type="button" aria-pressed={chosen?.mode === "even"} onClick={() => choose({ mode: "even" })}>
          Evenly
        </button>
        <button type="button" disabled={!queue.canUndo} onClick={() => queue.undo()}>
          Undo
        </button>
      </div>

      {lines.length > 1 && (
        <div className="item-nav">
          <button
            type="button"
            disabled={queue.current === 0}
            onClick={() => {
              setSelected(null);
              queue.jump(queue.current - 1);
            }}
          >
            ‹ Previous
          </button>
          <button
            type="button"
            disabled={queue.current >= lines.length - 1}
            onClick={() => {
              setSelected(null);
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
        </div>
      )}
    </div>
  );
}
