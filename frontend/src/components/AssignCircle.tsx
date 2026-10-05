import { useRef, useState } from "react";
import type { DocumentLine } from "../api";
import { formatCents } from "../format";
import { assignLabel, type Person } from "../split";
import { useCardQueue } from "../useCardQueue";
import { type Flight, ItemName, PeopleCircle } from "./PeopleCircle";

/** Who shares a line: seat indexes, or (tax, tip, fees only) "byOrder": by what each ordered. */
export type Pick = number[] | "byOrder";
/** One pick per row number (JSON-style keys). */
export type Picks = Record<string, Pick>;
/** Seat indexes per item row, as the API expects. */
export type Assignments = Record<string, number[]>;

interface AssignCircleProps {
  people: Person[];
  /** Items and discounts to assign. */
  lines: DocumentLine[];
  /** Tax, tips, and fees: assigned the same way, after the items. */
  chargeLines?: DocumentLine[];
  currency: string;
  initial?: Picks;
  onDone: (picks: Picks) => void;
}

const ITEM_KINDS = new Set(["item", "discount"]);

/**
 * Every line one at a time in the middle of the circle: items, then tax, tips, and fees. Drag
 * a card onto someone, or tap people and confirm ("Give to Bao" / "Split among Bao & Linh").
 * Tax, tips, and fees also offer "By what's ordered". Everyone, ‹ ›, and a list to revisit.
 */
export function AssignCircle({
  people,
  lines,
  chargeLines = [],
  currency,
  initial = {},
  onDone,
}: AssignCircleProps) {
  const charges = new Set(chargeLines.map((l) => l.row));
  const cards = [
    ...lines.filter((l) => ITEM_KINDS.has(l.kind)),
    ...chargeLines,
  ];
  const queue = useCardQueue<Pick>(
    cards.map((l) => String(l.row)),
    initial,
    onDone,
  );
  // Tapped people for the card on screen. A card that already has people starts with them
  // selected (their rings and the card's border show who has it); tapping changes that.
  const [tapped, setTapped] = useState<{ row: number; seats: number[] } | null>(null);
  const [flight, setFlight] = useState<Flight | null>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const card = cards[queue.current];
  const picks = queue.values;
  const seatsOf = (row: number): number[] => {
    const pick = picks[String(row)];
    return Array.isArray(pick) ? pick : [];
  };
  const selected = !card ? [] : tapped?.row === card.row ? tapped.seats : seatsOf(card.row);
  const setSelected = (seats: number[]) => setTapped(card ? { row: card.row, seats } : null);

  // Running amount per person: items, then charges (by what's ordered = weighted by items).
  const itemTotals = people.map((_, seat) =>
    cards
      .filter((l) => !charges.has(l.row))
      .reduce((sum, l) => {
        const seats = seatsOf(l.row);
        return seats.includes(seat) ? sum + l.amountCents / seats.length : sum;
      }, 0),
  );
  const itemSum = itemTotals.reduce((a, b) => a + Math.max(0, b), 0);
  const shareOf = (line: DocumentLine, seat: number): number => {
    const pick = picks[String(line.row)];
    if (pick === "byOrder") {
      return itemSum > 0
        ? (line.amountCents * Math.max(0, itemTotals[seat])) / itemSum
        : line.amountCents / people.length;
    }
    return pick?.includes(seat) ? line.amountCents / pick.length : 0;
  };
  const totals = people.map((_, seat) =>
    cards.reduce((sum, l) => sum + shareOf(l, seat), 0),
  );
  const done = cards.filter((l) => picks[String(l.row)] !== undefined).length;

  function choose(pick: Pick) {
    const seats =
      pick === "byOrder"
        ? people.map((_, i) => i)
        : [...pick].sort((a, b) => a - b);
    if (!seats.length) return;
    setFlight((f) => ({ seats, id: (f?.id ?? 0) + 1 }));
    setTapped(null);
    queue.choose(pick === "byOrder" ? pick : seats);
  }

  const toggle = (seat: number) =>
    setSelected(
      selected.includes(seat)
        ? selected.filter((s) => s !== seat)
        : [...selected, seat],
    );
  const jump = (i: number) => {
    setTapped(null);
    queue.jump(i);
  };

  const isCharge = card ? charges.has(card.row) : false;
  // A discount usually belongs to whoever got the item printed just above it.
  const above =
    card?.kind === "discount"
      ? [...cards]
          .reverse()
          .find(
            (l) =>
              l.kind === "item" && l.row < card.row && seatsOf(l.row).length,
          )
      : undefined;
  const nameOf = (seats: number[]) =>
    seats.map((s) => people[s]?.name).join(", ");

  // "Bao ($7.25), Linh ($7.25)": each person's share of that line, shown to the cent (the
  // saved split assigns any leftover cent exactly).
  const sharesOf = (line: DocumentLine) => {
    const pick = picks[String(line.row)];
    if (pick === "byOrder") return "by what's ordered";
    return (pick ?? [])
      .map(
        (s) =>
          `${people[s]?.name} (${formatCents(Math.round(line.amountCents / pick!.length), currency)})`,
      )
      .join(", ");
  };
  const left = cards.length - done;
  const currentPick = card ? picks[String(card.row)] : undefined;

  return (
    <div className="assign">
      {/* Equal space above and below the circle keeps it centered between the bar and the button. */}
      <div className="assign-main">
      <div className="circle-zone">
      <PeopleCircle
        people={people}
        currency={currency}
        amounts={totals}
        selected={selected}
        onSeat={toggle}
        onDrop={(seat) => choose([seat])}
        flight={flight}
        counter={`${Math.min(queue.current + 1, cards.length)} of ${cards.length}`}
        card={
          card && (
            <div
              key={card.row}
              // Tax, tips, and fees get their own tint so they don't read as an item.
              className={isCharge ? "item-card-content is-charge" : "item-card-content"}
            >
              {card.kind === "discount" && (
                <span className="item-kind">Discount</span>
              )}
              <ItemName text={card.description} price={formatCents(card.amountCents, currency)} />
              {currentPick === "byOrder" && (
                <span className="item-pick">By what's ordered</span>
              )}
            </div>
          )
        }
      />
      </div>

      {/* One steady main button: it fills in as people are tapped. */}
      <div className="assign-cta">
        <button
          type="button"
          className="primary cta-main"
          disabled={!selected.length}
          onClick={() => choose(selected)}
        >
          {selected.length
            ? assignLabel(
                [...selected].sort((a, b) => a - b).map((s) => people[s].name),
              )
            : "Drag & drop, or tap people to share"}
        </button>
      </div>

      <div className="assign-actions">
        {cards.length > 1 && (
          <button
            type="button"
            className="nav-arrow"
            aria-label="Previous item"
            disabled={queue.current === 0}
            onClick={() => jump(queue.current - 1)}
          >
            ‹
          </button>
        )}
        {isCharge && (
          <button
            type="button"
            className="chip-suggested"
            aria-pressed={currentPick === "byOrder"}
            onClick={() => choose("byOrder")}
          >
            By what's ordered
          </button>
        )}
        {above && (
          <button
            type="button"
            aria-label={`Same as ${above.description} (${nameOf(seatsOf(above.row))})`}
            onClick={() => choose(seatsOf(above.row))}
          >
            Same as above
          </button>
        )}
        <button type="button" onClick={() => choose(people.map((_, i) => i))}>
          Everyone
        </button>
        {cards.length > 1 && (
          <button
            type="button"
            className="nav-arrow"
            aria-label="Next item"
            disabled={queue.current >= cards.length - 1}
            onClick={() => jump(queue.current + 1)}
          >
            ›
          </button>
        )}
      </div>

      <div className="finish-actions">
        <button
          type="button"
          className="btn-block btn-split"
          disabled={!queue.complete}
          onClick={queue.finish}
        >
          {queue.complete ? "Finish split" : `Finish split · ${left} left`}
        </button>
      </div>

      </div>

      <button
        type="button"
        className="list-peek"
        onClick={() =>
          listRef.current?.scrollIntoView({
            behavior: "smooth",
            block: "start",
          })
        }
      >
        Everything and who has it <span aria-hidden="true">↓</span>
      </button>
      <ul className="assign-list" aria-label="Items" ref={listRef}>
        {cards.map((l, i) => (
          <li key={l.row}>
            <button
              type="button"
              className={
                i === queue.current ? "assign-row current" : "assign-row"
              }
              onClick={() => jump(i)}
            >
              <span>{l.description}</span>
              <span className="muted"> — {sharesOf(l) || "not assigned"}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
