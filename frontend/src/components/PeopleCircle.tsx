import { type CSSProperties, type ReactNode, useRef, useState } from "react";
import { formatCents } from "../format";
import { type Person, seatColor } from "../split";
import { Avatar } from "./Avatar";

/** A just-assigned item flying into these seats (the id restarts the animation). */
export interface Flight {
  seats: number[];
  id: number;
}

interface PeopleCircleProps {
  people: Person[];
  currency: string;
  /** Running amount under each person (cents, may be fractional); hidden when 0. */
  amounts?: number[];
  /** Selected seats (shown raised, with a ring). */
  selected?: number[];
  /** Tap on a person. */
  onSeat: (seat: number) => void;
  /** The card was dragged onto a person and released. */
  onDrop: (seat: number) => void;
  /** The card in the middle. */
  card?: ReactNode;
  flight?: Flight | null;
  /** "1 of 9", beside the card's grip (it replaces a progress bar). */
  counter?: string;
}

/** Seat 0 (you) at the bottom, then clockwise (left, top, right), like the table. */
const angleOf = (seat: number, count: number) =>
  // Three people: the other two sit near the top (upper left/right), clear of the card.
  count === 3 && seat > 0 ? Math.PI / 2 + (seat === 1 ? 145 : 215) * (Math.PI / 180) : Math.PI / 2 + (seat * 2 * Math.PI) / count;
const RADIUS = 0.46; // of the circle's width: people sit well out from the table disc

/** One border, one equal segment per color, starting at the top: "conic-gradient(...)". */
function ringGradient(colors: string[]): string {
  const step = 100 / colors.length;
  const stops = colors.map((c, i) => `${c} ${(i * step).toFixed(2)}% ${((i + 1) * step).toFixed(2)}%`);
  return `conic-gradient(${stops.join(", ")})`;
}

/** From this many people, a circle gets crowded: the card sits on top, people in a grid below. */
export const GRID_FROM = 7;
/** Names in the grid's narrow cells: longer ones end in "…" (the full name is its label). */
const GRID_NAME = 10;
const shortName = (name: string) =>
  name.length > GRID_NAME ? `${name.slice(0, GRID_NAME - 1).trimEnd()}…` : name;

/**
 * People around a circle with a card in the middle. Drag the card onto someone (they lift as
 * you hover), or tap people to select them. Assigned items fly into the avatar.
 */
export function PeopleCircle({
  people,
  currency,
  amounts = [],
  selected = [],
  onSeat,
  onDrop,
  card,
  flight = null,
  counter,
}: PeopleCircleProps) {
  const grid = people.length >= GRID_FROM;
  const start = useRef<{ x: number; y: number } | null>(null);
  const [drag, setDrag] = useState<{ dx: number; dy: number } | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);

  function seatUnder(x: number, y: number): number | null {
    if (typeof document.elementsFromPoint !== "function") return null;
    const el = document
      .elementsFromPoint(x, y)
      .find(
        (node) =>
          node instanceof HTMLElement && node.dataset.seat !== undefined,
      ) as HTMLElement | undefined;
    return el ? Number(el.dataset.seat) : null;
  }

  function onPointerDown(event: React.PointerEvent) {
    start.current = { x: event.clientX, y: event.clientY };
    (event.target as Element).setPointerCapture?.(event.pointerId);
  }
  function onPointerMove(event: React.PointerEvent) {
    if (!start.current) return;
    setDrag({
      dx: event.clientX - start.current.x,
      dy: event.clientY - start.current.y,
    });
    setHovered(seatUnder(event.clientX, event.clientY));
  }
  function onPointerUp(event: React.PointerEvent) {
    const moved = drag && Math.hypot(drag.dx, drag.dy) > 12;
    const target = moved ? seatUnder(event.clientX, event.clientY) : null;
    start.current = null;
    setDrag(null);
    setHovered(null);
    if (target !== null) onDrop(target);
  }

  // The card wears the color of whoever it's going to: the person under it while dragging,
  // or everyone tapped, as one border split into a segment per person.
  const ringSeats = drag && hovered !== null ? [hovered] : selected;
  const cardRing = ringSeats.length
    ? ({ "--ring": ringGradient(ringSeats.map(seatColor)) } as CSSProperties)
    : {};

  const cardEl = card && (
    <div
      className={[grid ? "item-card in-grid" : "item-card", drag ? "dragging" : "", ringSeats.length ? "ringed" : ""]
        .filter(Boolean)
        .join(" ")}
      data-testid="item-card"
      style={{
        ...cardRing,
        ...(drag
          ? {
              // Slightly smaller while dragging, so it hides less of the people.
              transform: grid
                ? `translate(${drag.dx}px, ${drag.dy}px) rotate(${drag.dx / 40}deg) scale(0.9)`
                : `translate(calc(-50% + ${drag.dx}px), calc(-50% + ${drag.dy}px)) rotate(${drag.dx / 40}deg) scale(0.9)`,
            }
          : {}),
      }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={() => {
        start.current = null;
        setDrag(null);
        setHovered(null);
      }}
    >
      {/* The grip says "drag me" (the dashed rings around people say "drop here"); beside it,
          which card this is. */}
      <span className="card-top">
        <span className="drag-grip" aria-hidden="true" />
        {counter && <span className="card-count">{counter}</span>}
      </span>
      {card}
      {/* Over someone: their avatar pops onto the card's corner, so neither is hidden. */}
      {drag && hovered !== null && people[hovered] && (
        // A key unlike the card content's (a row number), or React mixes the two up and
        // leaves copies of the card's text behind.
        <span key={`holder-${hovered}`} className="card-holder" aria-hidden="true">
          <Avatar name={people[hovered].name} seat={hovered} size="md" />
          <span className="card-holder-name" style={{ background: seatColor(hovered) }}>
            {people[hovered].name}
          </span>
        </span>
      )}
    </div>
  );

  if (grid) {
    // Many people: the card on top, everyone in a grid below (drag onto or tap, as in the circle).
    return (
      <div className={drag ? "people-board is-dragging" : "people-board"}>
        {cardEl}
        <div className="people-grid">
          {people.map((p, seat) => {
            const amount = amounts[seat] ? formatCents(Math.round(amounts[seat]), currency) : "";
            const received = flight?.seats.includes(seat);
            const classes = ["circle-seat", "grid-seat", hovered === seat ? "hovered" : "", received ? "received" : ""];
            return (
              <button
                key={p.name}
                type="button"
                className={classes.filter(Boolean).join(" ")}
                data-seat={seat}
                title={p.name}
                aria-label={amount ? `${p.name}, ${amount}` : p.name}
                aria-pressed={selected.includes(seat)}
                style={{ "--seat-color": seatColor(seat) } as CSSProperties}
                onClick={() => onSeat(seat)}
              >
                <span key={received ? `r${flight?.id}` : "still"} className="avatar-wrap">
                  <Avatar name={p.name} seat={seat} size="lg" />
                </span>
                <span className="circle-name">{shortName(p.name)}</span>
                {amount && <span className="circle-amount">{amount}</span>}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div className={drag ? "circle is-dragging" : "circle"}>
      {people.map((p, seat) => {
        const angle = angleOf(seat, people.length);
        const amount = amounts[seat]
          ? formatCents(Math.round(amounts[seat]), currency)
          : "";
        const received = flight?.seats.includes(seat);
        // Labels point away from the card: above for the top half, below for the bottom half.
        const upper = Math.sin(angle) < -0.2;
        const classes = [
          "circle-seat",
          upper ? "label-above" : "label-below",
          hovered === seat ? "hovered" : "",
          received ? "received" : "",
        ];
        return (
          <button
            key={p.name}
            type="button"
            className={classes.filter(Boolean).join(" ")}
            data-seat={seat}
            aria-label={amount ? `${p.name}, ${amount}` : p.name}
            aria-pressed={selected.includes(seat)}
            style={
              {
                left: `${50 + RADIUS * 100 * Math.cos(angle)}%`,
                top: `${50 + RADIUS * 100 * Math.sin(angle)}%`,
                "--seat-color": seatColor(seat), // rings match the person's avatar
              } as CSSProperties
            }
            onClick={() => onSeat(seat)}
          >
            {/* A new key replays the "received" bounce for every assignment. */}
            <span
              key={received ? `r${flight?.id}` : "still"}
              className="avatar-wrap"
            >
              <Avatar name={p.name} seat={seat} size="lg" />
            </span>
            <span className="circle-label">
              <span className="circle-name">{p.name}</span>
              {amount && (
                <>
                  <span className="circle-sep" aria-hidden="true">
                    ·
                  </span>
                  <span className="circle-amount">{amount}</span>
                </>
              )}
            </span>
          </button>
        );
      })}

      {flight?.seats.map((seat) => {
        const angle = angleOf(seat, people.length);
        const style = {
          // cqw = % of the circle's width (it's a size container), so no measuring is needed.
          "--dx": `${(RADIUS * 100 * Math.cos(angle)).toFixed(2)}cqw`,
          "--dy": `${(RADIUS * 100 * Math.sin(angle)).toFixed(2)}cqw`,
        } as CSSProperties;
        return (
          <span
            key={`${flight.id}-${seat}`}
            className="fly"
            style={style}
            aria-hidden="true"
          />
        );
      })}

      {cardEl}
    </div>
  );
}

// About two lines of the card: longer names show their beginning, then "…", then the price.
const SHORT_NAME = 22;

/** "Pad Thai · $14.50" as one line of text: the price follows the name. A long name shows its
 *  beginning; "View more" shows it all (the card may then reach past the circle). */
export function ItemName({ text, price }: { text: string; price: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > SHORT_NAME;
  const cut = text.slice(0, SHORT_NAME + 1);
  const shown = long && !open ? `${cut.slice(0, Math.max(cut.lastIndexOf(" "), 12)).trimEnd()}…` : text;
  return (
    <span className={open ? "item-line open" : "item-line"}>
      <span className="item-name" title={long ? text : undefined}>
        {shown}
      </span>{" "}
      <span className="item-sep" aria-hidden="true">
        ·
      </span>{" "}
      <strong className="item-price">{price}</strong>
      {long && (
        <button
          type="button"
          className="item-more"
          aria-expanded={open}
          // Don't start dragging the card when tapping this.
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            setOpen((o) => !o);
          }}
        >
          {open ? "View less" : "View more"}
        </button>
      )}
    </span>
  );
}
