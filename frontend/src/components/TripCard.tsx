import type { TripSummary } from "../api";
import { formatCents } from "../format";

interface TripCardProps {
  active: TripSummary | null;
  past: TripSummary[];
  onOpen: (tripId: string) => void;
  onStart: () => void;
}

const receipts = (n = 0) => `${n} receipt${n === 1 ? "" : "s"}`;

/** The trip that's on (every scan joins it), or "Start a trip"; past trips underneath. */
export function TripCard({ active, past, onOpen, onStart }: TripCardProps) {
  return (
    <div className="trips">
      {active ? (
        <button type="button" className="trip-card" onClick={() => onOpen(active.tripId)}>
          <span className="trip-card-label">Trip on · new scans join it</span>
          <strong className="trip-card-name">{active.name}</strong>
          <span className="trip-card-meta">
            {receipts(active.receiptCount)} · {formatCents(active.totalCents ?? 0, "USD")}
          </span>
        </button>
      ) : (
        <button type="button" className="link-button trip-start" onClick={onStart}>
          + Start a trip
        </button>
      )}
      {past.length > 0 && (
        <ul className="plain-list trip-past" aria-label="Past trips">
          {past.slice(0, 3).map((trip) => (
            <li key={trip.tripId}>
              <button type="button" className="link-button" onClick={() => onOpen(trip.tripId)}>
                {trip.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
