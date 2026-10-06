import { type CSSProperties, useState } from "react";
import type { DocumentLine } from "../api";
import { useBackLayer } from "../backButton";
import { formatCents } from "../format";
import { type Person, seatColor } from "../split";
import { Avatar } from "./Avatar";
import { LinePicker } from "./LinePicker";

/** Who shares a line: seat indexes, or (tax, tips, fees only) "byOrder": by what each ordered. */
export type Pick = number[] | "byOrder";
/** One pick per row number (JSON-style keys). */
export type Picks = Record<string, Pick>;
/** Seat indexes per item row, as the API expects. */
export type Assignments = Record<string, number[]>;

interface SplitListProps {
  people: Person[];
  /** Items and discounts to assign. */
  lines: DocumentLine[];
  /** Tax, tips, and fees: by what's ordered unless changed. */
  chargeLines?: DocumentLine[];
  currency: string;
  initial?: Picks;
  onDone: (picks: Picks) => void;
  onEditPeople?: () => void;
}

const ITEM_KINDS = new Set(["item", "discount"]);
const SHORT_NAME = 10;
const shortName = (name: string) =>
  name.length > SHORT_NAME
    ? `${name.slice(0, SHORT_NAME - 1).trimEnd()}…`
    : name;

/**
 * Every line in one list, for any number of people. One person is selected at a time (you,
 * first): each item tapped is theirs; "Next" hands over to the next person, so passing the
 * phone around the table is the same thing. With nobody selected, tapping a line opens the
 * picker. Tax, tips, and fees go by what's ordered unless changed (tap them any time).
 */
export function SplitList({
  people,
  lines,
  chargeLines = [],
  currency,
  initial = {},
  onDone,
  onEditPeople,
}: SplitListProps) {
  const items = lines.filter((l) => ITEM_KINDS.has(l.kind));
  const charges = new Set(chargeLines.map((l) => l.row));
  const [picks, setPicks] = useState<Picks>(() => {
    const start = { ...initial };
    for (const c of chargeLines) start[String(c.row)] ??= "byOrder";
    return start;
  });
  // You're selected first: "Tap what Me had".
  const [active, setActive] = useState<number | null>(people.length ? 0 : null);
  const [picking, setPicking] = useState<number | null>(null);
  useBackLayer(active !== null, () => setActive(null));
  useBackLayer(picking !== null, () => setPicking(null));
  const money = (cents: number) => formatCents(Math.round(cents), currency);

  const seatsOf = (row: number): number[] => {
    const pick = picks[String(row)];
    return Array.isArray(pick) ? pick : [];
  };

  function setPick(row: number, pick: Pick) {
    setPicks((current) => {
      const next = { ...current };
      const key = String(row);
      if (Array.isArray(pick) && !pick.length) {
        if (charges.has(row))
          next[key] = "byOrder"; // nobody chosen: back to the default
        else delete next[key];
      } else {
        next[key] = Array.isArray(pick)
          ? [...pick].sort((a, b) => a - b)
          : pick;
      }
      return next;
    });
  }

  const toggle = (row: number, seat: number) => {
    const seats = seatsOf(row);
    setPick(
      row,
      seats.includes(seat) ? seats.filter((s) => s !== seat) : [...seats, seat],
    );
  };

  // Running amount per person: items, then charges (by what's ordered = weighted by items).
  const itemTotals = people.map((_, seat) =>
    items.reduce((sum, l) => {
      const seats = seatsOf(l.row);
      return seats.includes(seat) ? sum + l.amountCents / seats.length : sum;
    }, 0),
  );
  const itemSum = itemTotals.reduce((a, b) => a + Math.max(0, b), 0);
  const chargeShare = (line: DocumentLine, seat: number) => {
    const pick = picks[String(line.row)];
    if (pick === "byOrder" || pick === undefined) {
      return itemSum > 0
        ? (line.amountCents * Math.max(0, itemTotals[seat])) / itemSum
        : 0;
    }
    return pick.includes(seat) ? line.amountCents / pick.length : 0;
  };
  const totals = people.map(
    (_, seat) =>
      itemTotals[seat] +
      chargeLines.reduce((sum, l) => sum + chargeShare(l, seat), 0),
  );

  const left = items.filter((l) => !seatsOf(l.row).length);
  const leftCents = left.reduce((sum, l) => sum + l.amountCents, 0);
  const everyone = people.map((_, i) => i);
  const splitRest = () => left.forEach((l) => setPick(l.row, everyone));

  const nameList = (seats: number[]) =>
    seats.map((s) => people[s]?.name).join(", ");
  const describeCharge = (row: number) => {
    const pick = picks[String(row)];
    if (pick === "byOrder" || pick === undefined) return "By what's ordered";
    if (pick.length === people.length) return "Everyone, evenly";
    return nameList(pick);
  };

  function tapLine(line: DocumentLine) {
    // Tax, tips, and fees always open the picker; items go to whoever is selected.
    if (active !== null && !charges.has(line.row)) toggle(line.row, active);
    else setPicking(line.row);
  }

  const pickingLine = [...items, ...chargeLines].find((l) => l.row === picking);

  return (
    <div className="split-list">
      <div className="people-bar" role="group" aria-label="People">
        {people.map((p, seat) => (
          <button
            key={p.name}
            type="button"
            className={active === seat ? "person painting" : "person"}
            style={{ "--seat-color": seatColor(seat) } as CSSProperties}
            aria-pressed={active === seat}
            aria-label={`${p.name}, ${money(totals[seat])}`}
            title={p.name}
            onClick={() => setActive(active === seat ? null : seat)}
          >
            <Avatar name={p.name} seat={seat} size="md" />
            <span className="person-name">{shortName(p.name)}</span>
            <span className="person-total">{money(totals[seat])}</span>
          </button>
        ))}
        {onEditPeople && (
          <button
            type="button"
            className="person person-edit"
            onClick={onEditPeople}
            aria-label="Add or remove people"
          >
            <span className="person-plus" aria-hidden="true">
              +
            </span>
            <span className="person-name">People</span>
          </button>
        )}
      </div>

      {active === null ? (
        <div className="split-tools">
          <span className="split-hint">
            Tap a line, or a person to pick their items
          </span>
        </div>
      ) : (
        <div
          className="split-banner"
          style={{ "--seat-color": seatColor(active) } as CSSProperties}
          role="status"
        >
          <Avatar name={people[active].name} seat={active} size="sm" />
          <span className="split-banner-text">
            {people[active].self ? (
              <>
                Tap what <strong>you</strong> had
              </>
            ) : (
              <>
                Tap what <strong>{people[active].name}</strong> had
              </>
            )}
          </span>
          {/* Next hands over; on the last person it's Done. (Tap the highlighted person to
              stop selecting at any point.) */}
          <button
            type="button"
            className="split-banner-next"
            onClick={() => setActive(active + 1 < people.length ? active + 1 : null)}
          >
            {active + 1 < people.length ? `Next: ${shortName(people[active + 1].name)} →` : "Done"}
          </button>
        </div>
      )}

      {/* "ITEMS" and how far along: the selected person's count, or how many are assigned */}
      <div className="split-lines-head">
        <span className="split-lines-label">Items</span>
        <span className="split-lines-count">
          {active !== null
            ? `${items.filter((l) => seatsOf(l.row).includes(active)).length} selected for ${people[active].self ? "you" : people[active].name}`
            : `${items.length - left.length} of ${items.length} assigned`}
        </span>
      </div>
      <ul className="split-lines" aria-label="Lines">
        {items.map((line) => {
          const seats = seatsOf(line.row);
          const mine = active !== null && seats.includes(active);
          return (
            <li key={line.row}>
              <button
                type="button"
                className={[
                  "split-line",
                  seats.length ? "" : "open",
                  mine ? "mine" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                style={
                  active !== null
                    ? ({ "--seat-color": seatColor(active) } as CSSProperties)
                    : undefined
                }
                aria-pressed={active !== null ? mine : undefined}
                aria-label={`${line.description}, ${money(line.amountCents)}, ${seats.length ? nameList(seats) : "nobody yet"}`}
                onClick={() => tapLine(line)}
              >
                <span className="split-line-name">
                  {line.kind === "discount" && (
                    <span className="split-line-tag">Discount</span>
                  )}
                  {line.description}
                </span>
                <span className="split-line-price">
                  {money(line.amountCents)}
                </span>
                <span className="split-line-who">
                  {seats.length ? (
                    <>
                      <span className="faces">
                        {seats.map((s) => (
                          <Avatar
                            key={s}
                            name={people[s].name}
                            seat={s}
                            size="sm"
                          />
                        ))}
                      </span>
                      {seats.length > 1 && (
                        <span className="split-line-each">
                          {money(line.amountCents / seats.length)} each
                        </span>
                      )}
                    </>
                  ) : (
                    <span className="who-empty">Who had this?</span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
        {chargeLines.map((line) => (
          <li key={line.row}>
            <button
              type="button"
              className="split-line charge"
              aria-label={`${line.description}, ${money(line.amountCents)}, ${describeCharge(line.row)}`}
              onClick={() => tapLine(line)}
            >
              <span className="split-line-name">{line.description}</span>
              <span className="split-line-price">
                {money(line.amountCents)}
              </span>
              <span className="split-line-who">
                <span className="split-line-mode">
                  {describeCharge(line.row)}
                </span>
                <span className="split-line-change" aria-hidden="true">
                  · Tap to change ›
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>

      <div className="split-footer">
        <span className={left.length ? "split-left" : "split-left done"}>
          {left.length ? (
            <>
              <strong>{money(leftCents)}</strong> left · {left.length}{" "}
              {left.length === 1 ? "line" : "lines"}
            </>
          ) : (
            "All assigned ✓"
          )}
        </span>
        <div className="split-footer-actions">
          {left.length > 0 && (
            <button type="button" onClick={splitRest}>
              Split the rest evenly
            </button>
          )}
          <button
            type="button"
            className="primary"
            disabled={left.length > 0}
            onClick={() => onDone(picks)}
          >
            Finish split
          </button>
        </div>
      </div>

      {pickingLine && (
        <LinePicker
          line={pickingLine}
          people={people}
          currency={currency}
          pick={picks[String(pickingLine.row)]}
          charge={charges.has(pickingLine.row)}
          shares={people.map((_, seat) =>
            charges.has(pickingLine.row)
              ? chargeShare(pickingLine, seat)
              : seatsOf(pickingLine.row).includes(seat)
                ? pickingLine.amountCents / seatsOf(pickingLine.row).length
                : 0,
          )}
          onChange={(pick) => setPick(pickingLine.row, pick)}
          onClose={() => setPicking(null)}
        />
      )}
    </div>
  );
}
