import type { ChargeChoice, DocumentLine } from "../api";
import { formatCents } from "../format";
import type { Person } from "../split";
import { useCardQueue } from "../useCardQueue";
import { PeopleCircle } from "./PeopleCircle";

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

  return (
    <div className="assign">
      <p className="muted assign-progress">
        {queue.current + 1} of {lines.length}
      </p>
      <PeopleCircle
        people={people}
        currency={currency}
        selected={chosen?.mode === "person" ? [chosen.member] : null}
        onSeat={(seat) => queue.choose({ mode: "person", member: seat })}
        card={
          card && (
            <>
              <span className="muted">{LABELS[card.kind]}</span>
              <span className="item-name">{card.description}</span>
              <strong>{formatCents(card.amountCents, currency)}</strong>
            </>
          )
        }
      />
      <p className="muted assign-hint">Split it, or tap (or drag to) the one person paying it all.</p>
      <div className="assign-actions">
        <button
          type="button"
          className="primary"
          aria-pressed={chosen?.mode === "proportional"}
          onClick={() => queue.choose({ mode: "proportional" })}
        >
          By what each ordered
        </button>
        <button type="button" aria-pressed={chosen?.mode === "even"} onClick={() => queue.choose({ mode: "even" })}>
          Evenly
        </button>
        <button type="button" disabled={!queue.canUndo} onClick={() => queue.undo()}>
          Undo
        </button>
        {queue.complete && (
          <button type="button" className="primary" onClick={queue.finish}>
            Next
          </button>
        )}
      </div>
    </div>
  );
}
