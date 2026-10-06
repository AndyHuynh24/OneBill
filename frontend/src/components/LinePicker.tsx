import type { CSSProperties } from "react";
import { createPortal } from "react-dom";
import type { DocumentLine } from "../api";
import { formatCents } from "../format";
import { type Person, seatColor } from "../split";
import { Avatar } from "./Avatar";
import type { Pick } from "./SplitList";

interface LinePickerProps {
  line: DocumentLine;
  people: Person[];
  currency: string;
  pick: Pick | undefined;
  /** Tax, tip, or fee: also offers "By what's ordered". */
  charge: boolean;
  /** What each person owes for this line right now (cents, may be fractional). */
  shares?: number[];
  onChange: (pick: Pick) => void;
  onClose: () => void;
}

/** "Who had this?": everyone as a big tap target with what they owe for it, plus Everyone (and,
 *  for charges, by what's ordered). */
export function LinePicker({
  line,
  people,
  currency,
  pick,
  charge,
  shares = [],
  onChange,
  onClose,
}: LinePickerProps) {
  const everyone = people.map((_, i) => i);
  // By what's ordered includes everyone (weighted by their items), so it shows everyone selected;
  // tapping someone then switches to an explicit pick of the others.
  const seats = pick === "byOrder" ? everyone : Array.isArray(pick) ? pick : [];
  const toggle = (seat: number) =>
    onChange(
      seats.includes(seat) ? seats.filter((s) => s !== seat) : [...seats, seat],
    );

  // Drawn on the page itself, not inside the scrolling sheet, so it stays at the bottom of the
  // screen however far the list has been scrolled.
  return createPortal(
    <div className="picker-backdrop" onClick={onClose}>
      <div
        className="picker"
        role="dialog"
        aria-modal="true"
        aria-label={`Who had ${line.description}?`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="picker-head">
          <span className="picker-line">{line.description}</span>
          <strong>{formatCents(line.amountCents, currency)}</strong>
        </div>
        <div className="picker-people">
          {people.map((p, seat) => (
            <button
              key={p.name}
              type="button"
              className="picker-person"
              style={{ "--seat-color": seatColor(seat) } as CSSProperties}
              aria-pressed={seats.includes(seat)}
              onClick={() => toggle(seat)}
            >
              <Avatar name={p.name} seat={seat} size="md" />
              <span className="picker-name">{p.name}</span>
              <span className="picker-share">
                {shares[seat]
                  ? formatCents(Math.round(shares[seat]), currency)
                  : "—"}
              </span>
            </button>
          ))}
        </div>
        <div className="picker-actions">
          {charge && (
            <button
              type="button"
              aria-pressed={pick === "byOrder"}
              onClick={() => onChange("byOrder")}
            >
              By what's ordered
            </button>
          )}
          <button
            type="button"
            aria-pressed={Array.isArray(pick) && pick.length === people.length}
            onClick={() => onChange(everyone)}
          >
            Everyone
          </button>
          <button
            type="button"
            className="primary picker-done"
            onClick={onClose}
          >
            Done
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
