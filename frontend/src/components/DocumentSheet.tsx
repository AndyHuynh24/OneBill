import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { CorrectionFields, DocumentDetail, DocumentLine, OneTallyApi, ReviewRequest } from "../api";
import { centsToInput, DEADLINE_LABELS, formatCents, formatDate, parseDollars } from "../format";

const ITEM_KINDS = new Set(["item", "discount", "fee"]);
const SUMMARY_KINDS = new Set(["subtotal", "tax", "tip", "total", "payment", "change", "balance_due"]);

interface DocumentSheetProps {
  api: OneTallyApi;
  docId: string;
  onClose: () => void;
}

/** Detail view as a bottom sheet: what was read, why it may need a look, and Looks right / Fix. */
export function DocumentSheet({ api, docId, onClose }: DocumentSheetProps) {
  const queryClient = useQueryClient();
  const { data: doc, error, isPending } = useQuery({
    queryKey: ["document", docId],
    queryFn: () => api.getDocument(docId),
  });
  const [editing, setEditing] = useState(false);

  const review = useMutation({
    mutationFn: (request: ReviewRequest) => api.reviewDocument(docId, request),
    onSuccess: () => {
      for (const key of [["documents"], ["obligations"], ["document", docId]]) {
        queryClient.invalidateQueries({ queryKey: key });
      }
      onClose();
    },
  });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const currency = doc?.currency ?? "USD";
  const reviewable = doc?.status === "ready" || doc?.status === "needs_review";

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <section
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sheet-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="sheet-header">
          <h2 id="sheet-title">{doc?.vendor || doc?.filename || "Document"}</h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>

        {isPending && <p className="muted">Loading…</p>}
        {error && (
          <p role="alert" className="error">
            Couldn't load this document.
          </p>
        )}

        {doc && (
          <>
            {/* Missing fields are simply left out: a receipt without a printed date isn't an error. */}
            {(doc.documentDate || doc.totalCents != null) && (
              <p className="sheet-meta">
                {doc.documentDate && formatDate(doc.documentDate)}
                {doc.documentDate && doc.totalCents != null && " · "}
                {doc.totalCents != null && <strong>{formatCents(doc.totalCents, currency)}</strong>}
              </p>
            )}

            {doc.status === "failed" && <p className="notice notice-error">{doc.failureReason}</p>}

            {!!doc.issues?.length && (
              <div className="notice notice-warn">
                <strong>Worth a look</strong>
                <ul>
                  {doc.issues.map((issue) => (
                    <li key={issue}>{issue}</li>
                  ))}
                </ul>
              </div>
            )}

            {editing ? (
              <ReviewForm
                doc={doc}
                saving={review.isPending}
                onCancel={() => setEditing(false)}
                onSave={(fields) => review.mutate({ action: "correct", fields })}
              />
            ) : (
              <>
                <LineTable
                  label="Items"
                  lines={(doc.lines ?? []).filter((l) => ITEM_KINDS.has(l.kind))}
                  currency={currency}
                />
                <LineTable
                  label="Summary"
                  lines={(doc.lines ?? []).filter((l) => SUMMARY_KINDS.has(l.kind))}
                  currency={currency}
                  showKind
                />
                {!!doc.obligations?.length && (
                  <div className="sheet-section">
                    <h3>Deadlines</h3>
                    <ul className="plain-list">
                      {doc.obligations.map((o) => (
                        <li key={`${o.kind}-${o.dueDate}`}>
                          {DEADLINE_LABELS[o.kind] ?? o.kind}: {formatDate(o.dueDate)}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {reviewable && (
                  <div className="sheet-actions">
                    <button
                      type="button"
                      className="primary"
                      disabled={review.isPending}
                      onClick={() => review.mutate({ action: "accept" })}
                    >
                      Looks right
                    </button>
                    <button type="button" onClick={() => setEditing(true)}>
                      Fix
                    </button>
                  </div>
                )}
              </>
            )}
            {review.error && (
              <p role="alert" className="error">
                {review.error.message}
              </p>
            )}
          </>
        )}
      </section>
    </div>
  );
}

const KIND_NAMES: Record<string, string> = {
  subtotal: "Subtotal",
  tax: "Tax",
  tip: "Tip",
  total: "Total",
  payment: "Paid",
  change: "Change",
  balance_due: "Amount due",
};

function LineTable({
  label,
  lines,
  currency,
  showKind = false,
}: {
  label: string;
  lines: DocumentLine[];
  currency: string;
  showKind?: boolean;
}) {
  if (!lines.length) return null;
  return (
    <div className="sheet-section">
      <h3>{label}</h3>
      <table className="lines" aria-label={label}>
        <tbody>
          {lines.map((line) => (
            <tr key={line.row}>
              <td>
                {line.description}
                {showKind && <span className="kind"> {KIND_NAMES[line.kind]}</span>}
              </td>
              <td className="amount">{formatCents(line.amountCents, currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface DeadlineDraft {
  kind: string;
  dueDate: string;
}

function ReviewForm({
  doc,
  saving,
  onSave,
  onCancel,
}: {
  doc: DocumentDetail;
  saving: boolean;
  onSave: (fields: CorrectionFields) => void;
  onCancel: () => void;
}) {
  const originalDeadlines: DeadlineDraft[] = (doc.obligations ?? []).map((o) => ({ kind: o.kind, dueDate: o.dueDate }));
  const [vendor, setVendor] = useState(doc.vendor ?? "");
  const [date, setDate] = useState(doc.documentDate ?? "");
  const [total, setTotal] = useState(doc.totalCents != null ? centsToInput(doc.totalCents) : "");
  const [deadlines, setDeadlines] = useState<DeadlineDraft[]>(originalDeadlines);
  const [problem, setProblem] = useState<string | null>(null);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const fields: CorrectionFields = {};
    if (vendor.trim() !== (doc.vendor ?? "")) fields.vendor = vendor.trim() || null;
    if (date !== (doc.documentDate ?? "")) fields.documentDate = date || null;
    if (total !== (doc.totalCents != null ? centsToInput(doc.totalCents) : "")) {
      const cents = parseDollars(total);
      if (cents === null) {
        setProblem("Enter the total as an amount like 26.81.");
        return;
      }
      fields.totalCents = cents;
    }
    if (deadlines.some((d) => !d.dueDate)) {
      setProblem("Each deadline needs a date.");
      return;
    }
    if (JSON.stringify(deadlines) !== JSON.stringify(originalDeadlines)) fields.deadlines = deadlines;
    if (!Object.keys(fields).length) {
      setProblem("Nothing changed. Use Looks right if everything is correct.");
      return;
    }
    setProblem(null);
    onSave(fields);
  }

  const updateDeadline = (index: number, patch: Partial<DeadlineDraft>) =>
    setDeadlines((current) => current.map((d, i) => (i === index ? { ...d, ...patch } : d)));

  return (
    <form className="review-form" onSubmit={submit}>
      <label>
        Vendor
        <input value={vendor} onChange={(e) => setVendor(e.target.value)} maxLength={200} />
      </label>
      <label>
        Date
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      </label>
      <label>
        Total
        <input inputMode="decimal" value={total} onChange={(e) => setTotal(e.target.value)} />
      </label>

      <fieldset>
        <legend>Deadlines</legend>
        {deadlines.map((d, i) => (
          <div key={i} className="deadline-row">
            <select
              aria-label={`Deadline ${i + 1} type`}
              value={d.kind}
              onChange={(e) => updateDeadline(i, { kind: e.target.value })}
            >
              {Object.entries(DEADLINE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <input
              type="date"
              aria-label={`Deadline ${i + 1} date`}
              value={d.dueDate}
              onChange={(e) => updateDeadline(i, { dueDate: e.target.value })}
            />
            <button
              type="button"
              aria-label={`Remove deadline ${i + 1}`}
              onClick={() => setDeadlines((current) => current.filter((_, j) => j !== i))}
            >
              Remove
            </button>
          </div>
        ))}
        {deadlines.length < 10 && (
          <button type="button" onClick={() => setDeadlines((current) => [...current, { kind: "return_by", dueDate: "" }])}>
            Add deadline
          </button>
        )}
      </fieldset>

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
