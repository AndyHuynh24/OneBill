import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { BillingCycle, OneTallyApi, Subscription, SubscriptionEdit } from "../api";
import { centsToInput, formatCents, formatDate, parseDollars } from "../format";
import { cycleSuffix } from "../subscriptions";

const CYCLE_LABELS: Record<BillingCycle, string> = {
  weekly: "Weekly",
  monthly: "Monthly",
  quarterly: "Every 3 months",
  yearly: "Yearly",
  other: "Other",
};

interface SubscriptionSheetProps {
  api: OneTallyApi;
  subscription: Subscription;
  onClose: () => void;
}

/** One subscription: what the emails said, its charge history, and Fix / Mark cancelled. */
export function SubscriptionSheet({ api, subscription: s, onClose }: SubscriptionSheetProps) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"view" | "edit" | "confirm-cancel" | "confirm-delete">("view");
  const done = () => {
    for (const key of [["subscriptions"], ["obligations"]]) queryClient.invalidateQueries({ queryKey: key });
    onClose();
  };
  const cancel = useMutation({ mutationFn: () => api.cancelSubscription(s.serviceKey), onSuccess: done });
  const edit = useMutation({
    mutationFn: (fields: SubscriptionEdit) => api.editSubscription(s.serviceKey, fields),
    onSuccess: done,
  });
  const remove = useMutation({ mutationFn: () => api.deleteSubscription(s.serviceKey), onSuccess: done });
  const reminders = useMutation({
    mutationFn: (on: boolean) => api.setSubscriptionReminders(s.serviceKey, on),
    onSuccess: () => {
      for (const key of [["subscriptions"], ["obligations"]]) queryClient.invalidateQueries({ queryKey: key });
    },
  });
  // While saving, show the requested state; afterwards, what the server returned.
  const remindersOn = reminders.isPending
    ? reminders.variables === true
    : !(reminders.data?.remindersOff ?? s.remindersOff);
  const error = cancel.error ?? edit.error ?? remove.error ?? reminders.error;

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <section className="sheet" role="dialog" aria-modal="true" aria-labelledby="sub-title" onClick={(e) => e.stopPropagation()}>
        <header className="sheet-header">
          <h2 id="sub-title">{s.service}</h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>

        {mode === "edit" ? (
          <EditForm subscription={s} saving={edit.isPending} onSave={(f) => edit.mutate(f)} onCancel={() => setMode("view")} />
        ) : (
          <>
            <dl className="fields">
              {s.plan && (
                <>
                  <dt>Plan</dt>
                  <dd>{s.plan}</dd>
                </>
              )}
              <dt>Price</dt>
              <dd>{s.amountCents != null ? `${formatCents(s.amountCents, s.currency)}${cycleSuffix(s.cycle)}` : "Unknown"}</dd>
              <dt>{s.status === "trial_ending" ? "Trial ends" : s.category === "bill" ? "Next due date" : "Next renewal"}</dt>
              <dd>
                {s.status === "cancelled"
                  ? "Cancelled"
                  : s.nextRenewal
                    ? `${formatDate(s.nextRenewal)}${s.renewalComputed ? " (estimated)" : ""}`
                    : "Unknown"}
              </dd>
            </dl>
            {s.previousAmountCents != null && s.priceChangedAt && (
              <p className="notice notice-warn">
                Price changed from {formatCents(s.previousAmountCents, s.currency)} on {formatDate(s.priceChangedAt)}.
              </p>
            )}
            {!!s.issues.length && (
              <div className="notice notice-warn">
                <strong>Worth a look</strong>
                <ul>
                  {s.issues.map((issue) => (
                    <li key={issue}>{issue}</li>
                  ))}
                </ul>
              </div>
            )}
            {!!s.history.length && (
              <div className="sheet-section">
                <h3>Charges</h3>
                <table className="lines" aria-label="Charges">
                  <tbody>
                    {[...s.history].reverse().map((h) => (
                      <tr key={`${h.date}-${h.amountCents}`}>
                        <td>{formatDate(h.date)}</td>
                        <td className="amount">{formatCents(h.amountCents, s.currency)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {s.status !== "cancelled" && (
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={remindersOn}
                  disabled={reminders.isPending}
                  onChange={(e) => reminders.mutate(e.target.checked)}
                />
                {s.category === "bill" ? "Remind me before it's due" : "Remind me before renewals"}
              </label>
            )}
            {mode === "confirm-delete" ? (
              <div className="sheet-actions">
                <span>
                  Delete {s.service} and its reminders?
                  {s.source === "manual" ? "" : ` It can come back if new ${s.service} emails arrive.`}
                </span>
                <button type="button" className="primary" disabled={remove.isPending} onClick={() => remove.mutate()}>
                  Yes, delete
                </button>
                <button type="button" onClick={() => setMode("view")}>
                  Keep
                </button>
              </div>
            ) : mode === "confirm-cancel" ? (
              <div className="sheet-actions">
                <span>Stop reminders for {s.service}?</span>
                <button type="button" className="primary" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
                  Yes, stop reminders
                </button>
                <button type="button" onClick={() => setMode("view")}>
                  Keep
                </button>
              </div>
            ) : (
              <div className="sheet-actions">
                <button type="button" onClick={() => setMode("edit")}>
                  Fix
                </button>
                {s.status !== "cancelled" && (
                  <button type="button" onClick={() => setMode("confirm-cancel")}>
                    Mark cancelled
                  </button>
                )}
                <button type="button" className="danger" onClick={() => setMode("confirm-delete")}>
                  Delete subscription
                </button>
              </div>
            )}
          </>
        )}
        {error && (
          <p role="alert" className="error">
            {error.message}
          </p>
        )}
      </section>
    </div>
  );
}

function EditForm({
  subscription: s,
  saving,
  onSave,
  onCancel,
}: {
  subscription: Subscription;
  saving: boolean;
  onSave: (fields: SubscriptionEdit) => void;
  onCancel: () => void;
}) {
  const [price, setPrice] = useState(s.amountCents != null ? centsToInput(s.amountCents) : "");
  const [renewal, setRenewal] = useState(s.nextRenewal ?? "");
  const [cycle, setCycle] = useState<BillingCycle | "">(s.cycle ?? "");
  const [problem, setProblem] = useState<string | null>(null);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const fields: SubscriptionEdit = {};
    if (price !== (s.amountCents != null ? centsToInput(s.amountCents) : "")) {
      const cents = parseDollars(price);
      if (cents === null) return setProblem("Enter the price as an amount like 15.49.");
      fields.amountCents = cents;
    }
    if (renewal && renewal !== s.nextRenewal) fields.nextRenewal = renewal;
    if (cycle && cycle !== s.cycle) fields.cycle = cycle;
    if (!Object.keys(fields).length) return setProblem("Nothing changed.");
    setProblem(null);
    onSave(fields);
  }

  return (
    <form className="review-form" onSubmit={submit}>
      <label>
        Price
        <input inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
      </label>
      <label>
        Next renewal
        <input type="date" value={renewal} onChange={(e) => setRenewal(e.target.value)} />
      </label>
      <label>
        Billing
        <select value={cycle} onChange={(e) => setCycle(e.target.value as BillingCycle)}>
          <option value="" disabled>
            Choose…
          </option>
          {Object.entries(CYCLE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
      <div className="sheet-actions">
        <button type="submit" className="primary" disabled={saving}>
          Save
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
