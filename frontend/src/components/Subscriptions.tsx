import type { GmailConnection, Subscription } from "../api";
import { formatCents, relativeDay, timeAgo } from "../format";
import { activeCount, cycleLabel, monthlyCost, renewingWithin } from "../subscriptions";

interface SubscriptionsProps {
  subscriptions: Subscription[];
  connection: GmailConnection;
  today: string;
  now?: Date;
  /** Home shows the soonest few; the full page shows everything. */
  limit?: number;
  onOpen: (serviceKey: string) => void;
  onConnect: () => void;
  onAdd: () => void;
  onViewAll?: () => void;
}

/** Active count, next month's total, what renews in 30 days, and the soonest renewals. */
export function Subscriptions({
  subscriptions,
  connection,
  today,
  now = new Date(),
  limit,
  onOpen,
  onConnect,
  onAdd,
  onViewAll,
}: SubscriptionsProps) {
  const soon = renewingWithin(subscriptions, today, 30);
  const ordered = [...subscriptions].sort(
    (a, b) =>
      Number(a.status === "cancelled") - Number(b.status === "cancelled") ||
      (a.nextRenewal ?? "9999").localeCompare(b.nextRenewal ?? "9999"),
  );
  const shown = limit
    ? ordered.filter((s) => s.status !== "cancelled").slice(0, limit)
    : ordered;

  return (
    <div className="card summary-card">
      {subscriptions.length > 0 ? (
        <>
          <div className="stats">
            <div className="stat">
              <span className="stat-value">{activeCount(subscriptions)}</span>
              <span className="stat-label">Active</span>
            </div>
            <div className="stat">
              <span className="stat-value">{formatCents(soon.totalCents)}</span>
              <span className="stat-label">Next 30 days</span>
            </div>
            <div className="stat">
              <span className="stat-value">{formatCents(monthlyCost(subscriptions))}</span>
              <span className="stat-label">Per month</span>
            </div>
          </div>
          <table className="sub-table" aria-label="Subscriptions">
            <tbody>
              {shown.map((s) => (
                <tr
                  key={s.serviceKey}
                  className="sub-row"
                  onClick={() => onOpen(s.serviceKey)}
                >
                  <td>
                    <button
                      type="button"
                      onClick={(e) => (
                        e.stopPropagation(),
                        onOpen(s.serviceKey)
                      )}
                    >
                      {s.service}
                      <span className="sub-cycle">
                        {cycleLabel(s.cycle)}
                        {s.category === "bill" ? " · Bill" : ""}
                      </span>
                    </button>
                  </td>
                  <td className="sub-amount">
                    {s.amountCents != null
                      ? formatCents(s.amountCents, s.currency)
                      : "—"}
                    {s.previousAmountCents != null &&
                      s.status !== "cancelled" && (
                        <span className="chip chip-warn">
                          Up from{" "}
                          {formatCents(s.previousAmountCents, s.currency)}
                        </span>
                      )}
                  </td>
                  <td className="sub-when">{describe(s, now)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <p className="muted">
          No subscriptions yet. Connect Gmail and OneBill reads your
          subscription emails, or add rent and bills by hand.
        </p>
      )}

      <div className="summary-actions">
        {onViewAll && subscriptions.length > 0 && (
          <button type="button" className="link-button" onClick={onViewAll}>
            View all
          </button>
        )}
        <button type="button" onClick={onAdd}>
          + Add
        </button>
        {!connection.connected && (
          <button type="button" onClick={onConnect}>
            Connect Gmail
          </button>
        )}
      </div>
      {connection.connected && (
        <p className="muted connection">
          Gmail connected · last email{" "}
          {connection.lastUsedAt
            ? timeAgo(connection.lastUsedAt, now)
            : "not yet"}{" "}
          <button type="button" className="link-button" onClick={onConnect}>
            Manage
          </button>
        </p>
      )}
    </div>
  );
}

function describe(s: Subscription, now: Date): string {
  if (s.status === "cancelled") return "Cancelled";
  if (s.status === "payment_failed") return "Payment failed";
  if (!s.nextRenewal) return "—";
  const when = relativeDay(s.nextRenewal, now);
  if (s.status === "trial_ending") return `Trial ends ${when}`;
  return s.category === "bill" ? `Due ${when}` : when;
}
