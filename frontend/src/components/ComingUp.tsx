import type { GmailConnection, Subscription } from "../api";
import { formatCents, relativeDay } from "../format";
import { renewingWithin } from "../subscriptions";
import type { UpcomingItem } from "../upcoming";

interface ComingUpProps {
  items: UpcomingItem[];
  subscriptions: Subscription[];
  connection: GmailConnection;
  today: string;
  now?: Date;
  limit: number;
  onOpen: (item: UpcomingItem) => void;
  onViewAll: () => void;
  onAdd: () => void;
  onConnect: () => void;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Home: what's due soonest across subscriptions and bills, with a one-line total. */
export function ComingUp({ items, subscriptions, connection, today, now = new Date(), limit, onOpen, onViewAll, onAdd, onConnect }: ComingUpProps) {
  const soon = renewingWithin(subscriptions, today, 30);
  const shown = items.slice(0, limit);

  return (
    <div className="card coming">
      {soon.totalCents > 0 && (
        <p className="coming-total">
          <strong>{formatCents(soon.totalCents)}</strong> due in the next 30 days
        </p>
      )}
      {shown.length ? (
        <ul className="coming-list">
          {shown.map((item) => {
            const [, month, day] = item.date.split("-").map(Number);
            return (
              <li key={item.key}>
                <button type="button" className={item.warn ? "coming-row warn" : "coming-row"} onClick={() => onOpen(item)}>
                  <span className="coming-date" aria-hidden="true">
                    <span className="coming-month">{MONTHS[month - 1]}</span>
                    <span className="coming-day">{day}</span>
                  </span>
                  <span className="coming-what">
                    <span className="coming-name">{item.name}</span>
                    <span className="coming-when">
                      {/* A failed payment is about now, not its date. */}
                      {item.warn ? item.label : `${item.label} ${relativeDay(item.date, now)}`}
                    </span>
                  </span>
                  <span className="coming-amount">
                    {item.amountCents != null ? formatCents(item.amountCents, item.currency) : ""}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted coming-empty">
          Nothing coming up. Add rent and bills, or connect Gmail and OneBill finds your subscriptions.
        </p>
      )}
      <div className="coming-actions">
        <button type="button" onClick={onAdd}>
          + Add
        </button>
        {!connection.connected && (
          <button type="button" onClick={onConnect}>
            Connect Gmail
          </button>
        )}
        {items.length > 0 && (
          <button type="button" className="link-button coming-all" onClick={onViewAll}>
            View all
          </button>
        )}
      </div>
    </div>
  );
}
