import type { GmailConnection, Subscription } from "../api";
import { formatCents, relativeDay, timeAgo } from "../format";
import { cycleSuffix, nextMonthTotal } from "../subscriptions";

interface SubscriptionsProps {
  subscriptions: Subscription[];
  connection: GmailConnection;
  today: string;
  now?: Date;
  onOpen: (serviceKey: string) => void;
  onConnect: () => void;
  onAdd: () => void;
}

/** Home section: what renews when, and what next month will cost. */
export function Subscriptions({ subscriptions, connection, today, now = new Date(), onOpen, onConnect, onAdd }: SubscriptionsProps) {
  const next = nextMonthTotal(subscriptions, today);
  return (
    <div className="subscriptions">
      {subscriptions.length > 0 ? (
        <>
          <p className="next-month">
            Next month: <strong>{formatCents(next.totalCents)}</strong>
            <span className="muted">
              {" "}
              · {next.count} subscription{next.count === 1 ? "" : "s"}
            </span>
          </p>
          <ul className="doc-list">
            {subscriptions.map((s) => (
              <li key={s.serviceKey}>
                <button type="button" className="doc-card" onClick={() => onOpen(s.serviceKey)}>
                  <span className="doc-title">{s.service}</span>
                  <span className="doc-meta">
                    {s.amountCents != null ? `${formatCents(s.amountCents, s.currency)}${cycleSuffix(s.cycle)}` : "Price unknown"}
                    {" · "}
                    {describe(s, now)}
                  </span>
                  {s.previousAmountCents != null && s.status !== "cancelled" && (
                    <span className="chip chip-warn">Up from {formatCents(s.previousAmountCents, s.currency)}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="muted">
          No subscriptions yet. Connect Gmail and OneTally reads your subscription emails, or add rent and bills by hand.
        </p>
      )}
      <button type="button" onClick={onAdd}>
        + Add
      </button>
      {connection.connected ? (
        <p className="muted connection">
          Gmail connected · last email {connection.lastUsedAt ? timeAgo(connection.lastUsedAt, now) : "not yet"}{" "}
          <button type="button" className="link-button" onClick={onConnect}>
            Manage
          </button>
        </p>
      ) : (
        <button type="button" onClick={onConnect}>
          Connect Gmail
        </button>
      )}
    </div>
  );
}

function describe(s: Subscription, now: Date): string {
  if (s.status === "cancelled") return "Cancelled";
  if (s.status === "payment_failed") return "Payment failed";
  if (!s.nextRenewal) return "Renewal date unknown";
  const when = relativeDay(s.nextRenewal, now);
  if (s.status === "trial_ending") return `Trial ends ${when}`;
  return s.category === "bill" ? `Due ${when}` : `Renews ${when}`;
}
