import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { OneBillApi, TripPayment } from "../api";
import { formatCents, formatDate } from "../format";
import { evenSplit } from "../split";

interface TripSheetProps {
  api: OneBillApi;
  tripId: string;
  onClose: () => void;
  onOpenDocument: (docId: string) => void;
}

/** A trip: who pays whom to settle up (ticked as paid), each person's share, the receipts. */
export function TripSheet({ api, tripId, onClose, onOpenDocument }: TripSheetProps) {
  const queryClient = useQueryClient();
  const { data: trip, error } = useQuery({ queryKey: ["trip", tripId], queryFn: () => api.getTrip(tripId) });
  const [confirmDelete, setConfirmDelete] = useState(false);
  const refresh = () => {
    for (const key of [["trip", tripId], ["trips"], ["documents"]]) queryClient.invalidateQueries({ queryKey: key });
  };

  const settle = useMutation({
    mutationFn: ({ payment, paid }: { payment: TripPayment; paid: boolean }) =>
      api.setTripSettled(tripId, { from: payment.from, to: payment.to, paid }),
    onSuccess: (updated) => queryClient.setQueryData(["trip", tripId], updated),
  });
  const splitEvenly = useMutation({
    mutationFn: async (docId: string) => {
      const doc = await api.getDocument(docId);
      const people = (trip?.members ?? []).map((m) => (m.self ? { name: m.name, self: true } : { name: m.name }));
      return api.saveSplit(docId, evenSplit(doc, people));
    },
    onSuccess: (_split, docId) => {
      queryClient.invalidateQueries({ queryKey: ["document", docId] });
      refresh();
    },
  });
  const end = useMutation({ mutationFn: () => api.updateTrip(tripId, { end: true }), onSuccess: refresh });
  const remove = useMutation({
    mutationFn: () => api.deleteTrip(tripId),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  const failure = settle.error ?? splitEvenly.error ?? end.error ?? remove.error;
  const currency = trip?.receipts.find((r) => r.currency)?.currency ?? "USD";
  const unsplit = new Set(trip?.unsplit ?? []);
  const anySplit = trip?.receipts.some((r) => r.split);

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <section className="sheet trip-sheet" role="dialog" aria-modal="true" aria-labelledby="trip-title" onClick={(e) => e.stopPropagation()}>
        <header className="sheet-header">
          <h2 id="trip-title">{trip?.name ?? "Trip"}</h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        {error && <p className="error">Couldn't load this trip.</p>}
        {!trip && !error && <p className="muted">Loading…</p>}
        {trip && (
          <>
            <p className="muted trip-meta">
              {formatDate(trip.startedAt.slice(0, 10))}
              {trip.endedAt ? ` – ${formatDate(trip.endedAt.slice(0, 10))}` : " · on now"} ·{" "}
              {formatCents(trip.totalCents, currency)}
            </p>

            <h3>Settle up</h3>
            {trip.balancesChanged && (
              <p className="notice notice-warn">Balances changed since you ticked some payments. Check the list.</p>
            )}
            {trip.payments.length > 0 ? (
              <ul className="plain-list settle-list" aria-label="Settle up">
                {trip.payments.map((p) => {
                  const label = `${p.from} pays ${p.to}`;
                  const paid = !!p.settledAt;
                  return (
                    <li key={label} className={paid ? "share-row share-paid" : "share-row"} aria-label={label}>
                      <div className="share-detail">
                        <strong>
                          {p.from} → {p.to}
                        </strong>
                        {paid && <span className="paid-label">Paid</span>}
                      </div>
                      <strong className="share-total">{formatCents(p.amountCents, currency)}</strong>
                      <button
                        type="button"
                        className="paid-toggle"
                        aria-pressed={paid}
                        aria-label={`Mark ${label} as ${paid ? "not paid" : "paid"}`}
                        disabled={settle.isPending}
                        onClick={() => settle.mutate({ payment: p, paid: !paid })}
                      >
                        {paid ? "✓" : ""}
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="muted">{anySplit ? "Everyone is even ✓" : "Split the receipts to see who owes whom."}</p>
            )}

            {Object.keys(trip.spent).length > 0 && (
              <>
                <h3>Each person's share</h3>
                <ul className="plain-list trip-spent" aria-label="Each person's share">
                  {Object.entries(trip.spent).map(([name, cents]) => (
                    <li key={name}>
                      <span>{name}</span>
                      <span>{formatCents(cents, currency)}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}

            <h3>Receipts</h3>
            {trip.receipts.length === 0 && (
              <p className="muted">No receipts yet. Use Scan for trip on the trip card, or add one from a receipt.</p>
            )}
            <ul className="plain-list trip-receipts">
              {trip.receipts.map((r) => (
                <li key={r.docId} className="trip-receipt">
                  <button type="button" className="doc-card" onClick={() => onOpenDocument(r.docId)}>
                    <span className="doc-title">{r.vendor ?? "Receipt"}</span>
                    <span className="doc-meta">
                      {r.documentDate ? `${formatDate(r.documentDate)} · ` : ""}
                      {r.totalCents != null ? formatCents(r.totalCents, r.currency ?? currency) : "Reading…"}
                      {r.paidBy ? ` · paid by ${r.paidBy}` : ""}
                    </span>
                  </button>
                  {unsplit.has(r.docId) && (
                    <button
                      type="button"
                      className="link-button"
                      aria-label={`Split ${r.vendor ?? "this receipt"} evenly`}
                      disabled={splitEvenly.isPending}
                      onClick={() => splitEvenly.mutate(r.docId)}
                    >
                      Split evenly
                    </button>
                  )}
                </li>
              ))}
            </ul>

            {failure && (
              <p role="alert" className="error">
                {failure.message}
              </p>
            )}
            <div className="sheet-actions">
              {trip.status === "active" && (
                <button type="button" className="primary" disabled={end.isPending} onClick={() => end.mutate()}>
                  End trip
                </button>
              )}
              {confirmDelete ? (
                <>
                  <button type="button" className="danger" onClick={() => remove.mutate()}>
                    Yes, delete
                  </button>
                  <button type="button" onClick={() => setConfirmDelete(false)}>
                    Keep it
                  </button>
                </>
              ) : (
                <button type="button" onClick={() => setConfirmDelete(true)}>
                  Delete trip
                </button>
              )}
            </div>
            {confirmDelete && <p className="muted">The receipts stay in OneBill; only the trip goes.</p>}
          </>
        )}
      </section>
    </div>
  );
}
