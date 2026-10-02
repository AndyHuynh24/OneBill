import { type ReactNode, useRef, useState } from "react";
import { formatCents } from "../format";
import type { Person } from "../split";
import { Avatar } from "./Avatar";

interface PeopleCircleProps {
  people: Person[];
  currency: string;
  /** Running amount under each person (cents, may be fractional); hidden when 0. */
  amounts?: number[];
  /** Seats selected while sharing (shown as pressed). */
  selected?: number[] | null;
  onSeat: (seat: number) => void;
  /** The card in the middle; drag it onto a person to choose them. */
  card?: ReactNode;
}

/**
 * People around a circle in seat order: you at the bottom, then clockwise (left, top, right),
 * like the table. A card sits in the middle; tap a person, or drag the card onto them.
 */
export function PeopleCircle({ people, currency, amounts = [], selected = null, onSeat, card }: PeopleCircleProps) {
  const [drag, setDrag] = useState<{ dx: number; dy: number } | null>(null);
  const start = useRef<{ x: number; y: number } | null>(null);

  function onPointerDown(event: React.PointerEvent) {
    start.current = { x: event.clientX, y: event.clientY };
    (event.target as Element).setPointerCapture?.(event.pointerId);
  }
  function onPointerMove(event: React.PointerEvent) {
    if (start.current) setDrag({ dx: event.clientX - start.current.x, dy: event.clientY - start.current.y });
  }
  function onPointerUp(event: React.PointerEvent) {
    const moved = drag && Math.hypot(drag.dx, drag.dy) > 12;
    start.current = null;
    setDrag(null);
    if (!moved || typeof document.elementsFromPoint !== "function") return;
    const target = document
      .elementsFromPoint(event.clientX, event.clientY)
      .find((el) => el instanceof HTMLElement && el.dataset.seat !== undefined) as HTMLElement | undefined;
    if (target) onSeat(Number(target.dataset.seat));
  }

  return (
    <div className="circle">
      {people.map((p, seat) => {
        const angle = Math.PI / 2 + (seat * 2 * Math.PI) / people.length;
        const amount = amounts[seat] ? formatCents(Math.round(amounts[seat]), currency) : "";
        return (
          <button
            key={p.name}
            type="button"
            className="circle-seat"
            data-seat={seat}
            aria-label={amount ? `${p.name}, ${amount}` : p.name}
            aria-pressed={selected ? selected.includes(seat) : undefined}
            style={{ left: `${50 + 40 * Math.cos(angle)}%`, top: `${50 + 40 * Math.sin(angle)}%` }}
            onClick={() => onSeat(seat)}
          >
            <Avatar name={p.name} seat={seat} />
            <span className="circle-name">{p.name}</span>
            {amount && <span className="circle-amount">{amount}</span>}
          </button>
        );
      })}
      {card && (
        <div
          className="item-card"
          data-testid="item-card"
          style={drag ? { transform: `translate(calc(-50% + ${drag.dx}px), calc(-50% + ${drag.dy}px))` } : undefined}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
        >
          {card}
        </div>
      )}
    </div>
  );
}
