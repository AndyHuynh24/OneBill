import { useState } from "react";
import type { Obligation } from "../api";
import { DEADLINE_LABELS, formatCents, formatDate, relativeDay } from "../format";

interface UpcomingProps {
  obligations: Obligation[];
  onRemove?: (obligation: Obligation) => void;
}

export function Upcoming({ obligations, onRemove }: UpcomingProps) {
  const [confirming, setConfirming] = useState<string | null>(null);
  if (!obligations.length) {
    return <p className="muted">Nothing due in the next 30 days.</p>;
  }
  return (
    <ul className="upcoming-list">
      {obligations.map((o) => {
        const kind = DEADLINE_LABELS[o.kind] ?? "Due";
        const vendor = o.vendor || "Unknown vendor";
        const label = `${kind}: ${vendor}`;
        const subscription = o.kind === "renewal" || o.kind === "trial_ends";
        return (
          <li key={o.oblId} className="upcoming-item">
            <span className="upcoming-what">{label}</span>
            <span className="upcoming-when">
              {formatDate(o.dueDate)} ({relativeDay(o.dueDate)})
              {o.amountCents != null ? ` · ${formatCents(o.amountCents)}` : ""}
            </span>
            {onRemove &&
              (confirming === o.oblId ? (
                <span className="upcoming-confirm">
                  {subscription ? `Stops reminders for ${o.vendor ?? "this subscription"}. ` : ""}
                  <button type="button" className="link-button" onClick={() => onRemove(o)}>
                    Remove
                  </button>{" "}
                  <button type="button" className="link-button" onClick={() => setConfirming(null)}>
                    Keep
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  className="icon-button upcoming-remove"
                  aria-label={`Remove reminder: ${kind} ${vendor}`}
                  onClick={() => setConfirming(o.oblId)}
                >
                  ×
                </button>
              ))}
          </li>
        );
      })}
    </ul>
  );
}
