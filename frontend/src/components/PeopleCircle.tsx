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
}

/** Seat 0 (you) at the bottom, then clockwise (left, top, right), like the table. */
const angleOf = (seat: number, count: number) =>
  Math.PI / 2 + (seat * 2 * Math.PI) / count;
const RADIUS = 0.4; // of the circle's width

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
}: PeopleCircleProps) {
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
  // or everyone tapped (stacked rings).
  const ringSeats = drag && hovered !== null ? [hovered] : selected;
  const cardRing: CSSProperties = ringSeats.length
    ? {
        borderColor: seatColor(ringSeats[0]),
        boxShadow: [
          ...ringSeats.map((seat, i) => `0 0 0 ${3 * (i + 1)}px ${seatColor(seat)}`),
          "0 18px 40px rgba(80, 50, 25, 0.22)",
        ].join(", "),
      }
    : {};

  return (
    <div className="circle">
      {people.map((p, seat) => {
        const angle = angleOf(seat, people.length);
        const amount = amounts[seat]
          ? formatCents(Math.round(amounts[seat]), currency)
          : "";
        const received = flight?.seats.includes(seat);
        const classes = [
          "circle-seat",
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
            <span className="circle-name">{p.name}</span>
            {amount && <span className="circle-amount">{amount}</span>}
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

      {card && (
        <div
          className={drag ? "item-card dragging" : "item-card"}
          data-testid="item-card"
          style={{
            ...cardRing,
            ...(drag
              ? {
                  // Slightly smaller while dragging, so it hides less of the circle.
                  transform: `translate(calc(-50% + ${drag.dx}px), calc(-50% + ${drag.dy}px)) rotate(${drag.dx / 40}deg) scale(0.9)`,
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
          {card}
          {/* Over someone: their avatar pops onto the card's corner, so neither is hidden. */}
          {drag && hovered !== null && people[hovered] && (
            <span key={hovered} className="card-holder" aria-hidden="true">
              <Avatar name={people[hovered].name} seat={hovered} size="md" />
              <span className="card-holder-name" style={{ background: seatColor(hovered) }}>
                {people[hovered].name}
              </span>
            </span>
          )}
        </div>
      )}
    </div>
  );
}
