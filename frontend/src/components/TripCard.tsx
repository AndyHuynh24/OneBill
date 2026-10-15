import type { TripSummary } from "../api";
import { formatCents } from "../format";
import { ScanButton } from "./ScanButton";

interface TripCardProps {
  active: TripSummary | null;
  past: TripSummary[];
  onOpen: (tripId: string) => void;
  onStart: () => void;
  /** The trip's own Scan: the receipt joins this trip (the main Scan button never does). */
  onScan: (file: File, tripId: string) => void;
  scanning?: boolean;
}

const receipts = (n = 0) => `${n} receipt${n === 1 ? "" : "s"}`;

/** The trip that's on, with its own Scan button, or "Start a trip"; past trips underneath. */
export function TripCard({ active, past, onOpen, onStart, onScan, scanning = false }: TripCardProps) {
  return (
    <div className="trips">
      {active ? (
        <div className="trip-card">
          <button
            type="button"
            className="trip-card-open"
            aria-label={`Open ${active.name}`}
            onClick={() => onOpen(active.tripId)}
          >
            <span className="trip-card-label">Trip on</span>
            <strong className="trip-card-name">{active.name}</strong>
            <span className="trip-card-meta">
              {receipts(active.receiptCount)} · {formatCents(active.totalCents ?? 0, "USD")}
            </span>
          </button>
          <ScanButton
            inline
            label="Scan for trip"
            purpose={`for ${active.name}`}
            busy={scanning}
            onFile={(file) => onScan(file, active.tripId)}
          />
        </div>
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
