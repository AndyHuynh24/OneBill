import type { Obligation } from "../api";
import { DEADLINE_LABELS, formatCents, formatDate, relativeDay } from "../format";

export function Upcoming({ obligations }: { obligations: Obligation[] }) {
  if (!obligations.length) {
    return <p className="muted">No deadlines in the next 30 days.</p>;
  }
  return (
    <ul className="upcoming-list">
      {obligations.map((o) => (
        <li key={o.oblId} className="upcoming-item">
          <span className="upcoming-what">
            {DEADLINE_LABELS[o.kind] ?? "Due"}: {o.vendor || "Unknown vendor"}
          </span>
          <span className="upcoming-when">
            {formatDate(o.dueDate)} ({relativeDay(o.dueDate)})
            {o.amountCents != null ? ` · ${formatCents(o.amountCents)}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}
