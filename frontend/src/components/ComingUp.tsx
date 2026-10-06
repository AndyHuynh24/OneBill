import type { GmailConnection, Subscription } from "../api";
import { daysUntil, formatCents, relativeDay } from "../format";
import { activeCount, renewingWithin } from "../subscriptions";
import type { UpcomingItem } from "../upcoming";

interface ComingUpProps {
  items: UpcomingItem[];
  subscriptions: Subscription[];
  connection: GmailConnection;
  today: string;
  now?: Date;
  onOpen: (item: UpcomingItem) => void;
  onViewAll: () => void;
  onAdd: () => void;
  onConnect: () => void;
  /** Reminder emails on or off (undefined while loading: no switch yet). */
  emailReminders?: boolean;
  onEmailReminders?: (on: boolean) => void;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Home: everything due in the next 30 days across subscriptions and bills, with a one-line total. */
export function ComingUp({ items, subscriptions, connection, today, now = new Date(), onOpen, onViewAll, onAdd, onConnect, emailReminders, onEmailReminders }: ComingUpProps) {
  const soon = renewingWithin(subscriptions, today, 30);
  // Everything due in the next 30 days, nothing later (that's on View all).
  const shown = items.filter((item) => daysUntil(item.date, now) <= 30);
  const hidden = items.length - shown.length;
  const active = activeCount(subscriptions);

  return (
    <div className="card coming">
      {/* Top line: what's due soon, and the reminder-email switch beside it */}
      <div className="coming-top">
        <p className="coming-total">
          {soon.totalCents > 0 && (
            <>
              <strong>{formatCents(soon.totalCents)}</strong> due in the next 30 days
            </>
          )}
        </p>
        {emailReminders !== undefined && onEmailReminders && (
          <label className="reminder-switch">
            <span className="reminder-switch-text">Email reminders</span>
            <input
              type="checkbox"
              role="switch"
              title="Emails 7, 3, and 1 day before"
              checked={emailReminders}
              onChange={(e) => onEmailReminders(e.target.checked)}
            />
          </label>
        )}
      </div>
      {hidden > 0 && (
        <p className="coming-more">
          {active} active subscription{active === 1 ? "" : "s"} · showing what's due in the next 30 days
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
          {items.length
            ? "Nothing due in the next 30 days."
            : "Nothing coming up. Add rent and bills, or connect Gmail and OneBill finds your subscriptions."}
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
            {hidden > 0 ? `View all ${active} →` : "View all"}
          </button>
        )}
      </div>
    </div>
  );
}
