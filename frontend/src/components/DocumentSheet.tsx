import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type {
  Confidence,
  CorrectionFields,
  DocumentDetail,
  DocumentLine,
  LineKind,
  OneTallyApi,
  ReviewRequest,
} from "../api";
import { centsToInput, DEADLINE_LABELS, formatCents, formatDate, parseDollars, parseSignedDollars } from "../format";
import { canSplit } from "../split";
import { ScanProgress } from "./ScanProgress";
import { SplitFlow } from "./SplitFlow";

const ITEM_KINDS = new Set(["item", "discount", "fee"]);
const SUMMARY_KINDS = new Set(["subtotal", "tax", "tip", "total", "payment", "change", "balance_due"]);

const isReading = (status?: string) => status === "pending_upload" || status === "processing";

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
    // Still being read: keep checking so the sheet turns into the bill the moment it's done.
    refetchInterval: (query) => (isReading(query.state.data?.status) ? 1500 : false),
  });
  const [mode, setMode] = useState<"view" | "edit" | "ask" | "split" | "confirm-delete">("view");
  const editing = mode === "edit";
  const setEditing = (on: boolean) => setMode(on ? "edit" : "view");

  const review = useMutation({
    mutationFn: (request: ReviewRequest) => api.reviewDocument(docId, request),
    onSuccess: (updated, request) => {
      for (const key of [["documents"], ["obligations"], ["document", docId]]) {
        queryClient.invalidateQueries({ queryKey: key });
      }
      // A bill that adds up can be split: ask right away instead of closing.
      if (request.action === "accept" && canSplit(updated.lines)) setMode("ask");
      else onClose();
    },
  });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // "Looks right" on one flagged line: stays on the bill, the highlight fades out.
  const confirm = useMutation({
    mutationFn: (row: number) => api.reviewDocument(docId, { action: "confirm", rows: [row] }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["document", docId], updated);
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });
  const checks = new Map((doc?.confidence?.checks ?? []).map((c) => [c.row, c.reason]));
  const lineTableProps = {
    checks,
    confirming: confirm.isPending ? confirm.variables : undefined,
    onConfirm: (row: number) => confirm.mutate(row),
  };

  const remove = useMutation({
    mutationFn: () => api.deleteDocument(docId),
    onSuccess: () => {
      for (const key of [["documents"], ["obligations"]]) queryClient.invalidateQueries({ queryKey: key });
      onClose();
    },
  });

  const currency = doc?.currency ?? "USD";
  const reviewable = doc?.status === "ready" || doc?.status === "needs_review";
  const splittable = !!doc && canSplit(doc.lines);

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
          <h2 id="sheet-title">{isReading(doc?.status) ? "Reading your bill" : doc?.vendor || doc?.filename || "Document"}</h2>
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

        {doc && isReading(doc.status) && <ScanProgress stage="reading" />}
        {doc && !isReading(doc.status) && (
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

            {mode === "view" && <ConfidenceBanner confidence={doc.confidence} />}

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

            {mode === "split" ? (
              <SplitFlow api={api} doc={doc} onCancel={() => setMode("view")} onClose={onClose} />
            ) : mode === "ask" ? (
              <div className="split-prompt">
                <h3>Split this bill?</h3>
                <div className="sheet-actions">
                  <button type="button" className="primary" onClick={() => setMode("split")}>
                    Split with friends
                  </button>
                  <button type="button" onClick={onClose}>
                    Just me
                  </button>
                </div>
              </div>
            ) : editing ? (
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
                  {...lineTableProps}
                />
                <LineTable
                  label="Summary"
                  lines={(doc.lines ?? []).filter((l) => SUMMARY_KINDS.has(l.kind))}
                  currency={currency}
                  showKind
                  {...lineTableProps}
                />
                {doc.taxIncluded && (
                  <p className="muted tax-note">
                    Prices include tax
                    {doc.taxCents ? ` (${formatCents(doc.taxCents, currency)} of the total)` : ""}.
                  </p>
                )}
                {!!doc.obligations?.some((o) => o.kind in DEADLINE_LABELS) && (
                  <div className="sheet-section">
                    <h3>Deadlines</h3>
                    <ul className="plain-list">
                      {doc.obligations.filter((o) => o.kind in DEADLINE_LABELS).map((o) => (
                        <li key={`${o.kind}-${o.dueDate}`}>
                          {DEADLINE_LABELS[o.kind] ?? o.kind}: {formatDate(o.dueDate)}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {reviewable && (
                  <div className="sheet-actions pair">
                    <button
                      type="button"
                      className="soft"
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
                {doc.status === "ready" && (
                  <button
                    type="button"
                    className="btn-block btn-split"
                    disabled={!splittable}
                    onClick={() => setMode("split")}
                  >
                    {!splittable ? "No items to split" : doc.split ? "View split" : "Split bill"}
                  </button>
                )}
              </>
            )}
            {mode === "view" && (
              <button type="button" className="btn-block btn-delete" onClick={() => setMode("confirm-delete")}>
                Delete
              </button>
            )}
            {mode === "confirm-delete" && (
              <div className="sheet-actions">
                <span>Delete this bill, its deadlines, and its photo?</span>
                <button type="button" className="btn-delete" disabled={remove.isPending} onClick={() => remove.mutate()}>
                  Yes, delete
                </button>
                <button type="button" onClick={() => setMode("view")}>
                  Keep
                </button>
              </div>
            )}
            {remove.error && (
              <p role="alert" className="error">
                {remove.error.message}
              </p>
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

/** One line on top of the bill: how sure the reading is. */
function ConfidenceBanner({ confidence }: { confidence?: Confidence }) {
  if (!confidence) return null; // bills read before phase 2d
  const flagged = confidence.checks.length;
  if (confidence.level === "confident") {
    return <p className="confidence confidence-ok">✓ Confident: every amount was found on the bill and they add up.</p>;
  }
  if (confidence.level === "confirmed") {
    return <p className="confidence confidence-ok">✓ Checked by you.</p>;
  }
  if (flagged) {
    return (
      <p className="confidence confidence-check">
        Please double-check {flagged === 1 ? "the highlighted amount" : `the ${flagged} highlighted amounts`}. Tap{" "}
        <strong>✓ Looks right</strong> on each one that's correct.
      </p>
    );
  }
  if (confidence.level === "mostly") {
    return <p className="confidence confidence-quiet">Read clearly and the totals add up.</p>;
  }
  return null; // "check" without a culprit: the "Worth a look" box already explains
}

function LineTable({
  label,
  lines,
  currency,
  showKind = false,
  checks,
  confirming,
  onConfirm,
}: {
  label: string;
  lines: DocumentLine[];
  currency: string;
  showKind?: boolean;
  /** Flagged rows and why ("Hard to read on the photo"). */
  checks: Map<number, string>;
  confirming?: number;
  onConfirm: (row: number) => void;
}) {
  if (!lines.length) return null;
  return (
    <div className="sheet-section">
      <h3>{label}</h3>
      <table className="lines" aria-label={label}>
        <tbody>
          {lines.map((line) => {
            const reason = checks.get(line.row);
            return (
              <tr key={line.row} className={reason ? "line-check" : undefined}>
                <td>
                  {line.description}
                  {showKind && <span className="kind"> {KIND_NAMES[line.kind]}</span>}
                  {reason && (
                    <span className="check-row">
                      <span className="check-reason">{reason}</span>
                      <button
                        type="button"
                        className="check-ok"
                        aria-label={`Looks right: ${line.description} ${formatCents(line.amountCents, currency)}`}
                        disabled={confirming !== undefined}
                        onClick={() => onConfirm(line.row)}
                      >
                        {confirming === line.row ? "Saving…" : "✓ Looks right"}
                      </button>
                    </span>
                  )}
                </td>
                <td className="amount">{formatCents(line.amountCents, currency)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

interface DeadlineDraft {
  kind: string;
  dueDate: string;
}

interface LineDraft {
  description: string;
  kind: LineKind;
  amount: string;
}

/** Every kind a line can be, in the words people use. */
const LINE_KIND_LABELS: Record<LineKind, string> = {
  item: "Item",
  discount: "Discount",
  fee: "Fee",
  tax: "Tax",
  tip: "Tip",
  subtotal: "Subtotal",
  total: "Total",
  payment: "Paid",
  change: "Change",
  balance_due: "Amount due",
  info: "Note (not money)",
};

const lineDrafts = (lines: DocumentLine[] = []): LineDraft[] =>
  lines.map((l) => ({ description: l.description, kind: l.kind, amount: centsToInput(l.amountCents) }));

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
  // Kinds no longer tracked (old "return by" reminders) are left out, so saving drops them.
  const originalDeadlines: DeadlineDraft[] = (doc.obligations ?? [])
    .filter((o) => o.kind in DEADLINE_LABELS)
    .map((o) => ({ kind: o.kind, dueDate: o.dueDate }));
  const [vendor, setVendor] = useState(doc.vendor ?? "");
  const [date, setDate] = useState(doc.documentDate ?? "");
  const [total, setTotal] = useState(doc.totalCents != null ? centsToInput(doc.totalCents) : "");
  const [deadlines, setDeadlines] = useState<DeadlineDraft[]>(originalDeadlines);
  const [lines, setLines] = useState<LineDraft[]>(() => lineDrafts(doc.lines));
  const [problem, setProblem] = useState<string | null>(null);
  const editsLines = !!doc.lines?.length;

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const fields: CorrectionFields = {};
    if (vendor.trim() !== (doc.vendor ?? "")) fields.vendor = vendor.trim() || null;
    if (date !== (doc.documentDate ?? "")) fields.documentDate = date || null;
    if (editsLines) {
      const parsed = [];
      for (const [i, line] of lines.entries()) {
        const amountCents = parseSignedDollars(line.amount);
        if (!line.description.trim()) {
          setProblem(`Line ${i + 1} needs a description.`);
          return;
        }
        if (amountCents === null) {
          setProblem(`Line ${i + 1}: enter an amount like 4.19.`);
          return;
        }
        parsed.push({ description: line.description.trim(), kind: line.kind, amountCents });
      }
      const original = (doc.lines ?? []).map(({ description, kind, amountCents }) => ({ description, kind, amountCents }));
      if (JSON.stringify(parsed) !== JSON.stringify(original)) fields.lines = parsed;
    } else if (total !== (doc.totalCents != null ? centsToInput(doc.totalCents) : "")) {
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

  const updateLine = (index: number, patch: Partial<LineDraft>) =>
    setLines((current) => current.map((l, i) => (i === index ? { ...l, ...patch } : l)));
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
      {editsLines ? (
        <fieldset>
          <legend>Lines</legend>
          {lines.map((line, i) => (
            <div key={i} className="line-row">
              <input
                aria-label={`Line ${i + 1} description`}
                value={line.description}
                maxLength={120}
                onChange={(e) => updateLine(i, { description: e.target.value })}
              />
              <input
                aria-label={`Line ${i + 1} amount`}
                inputMode="decimal"
                className="line-amount"
                value={line.amount}
                onChange={(e) => updateLine(i, { amount: e.target.value })}
              />
              <select
                aria-label={`Line ${i + 1} kind`}
                value={line.kind}
                onChange={(e) => updateLine(i, { kind: e.target.value as LineKind })}
              >
                {Object.entries(LINE_KIND_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="icon-button"
                aria-label={`Remove line ${i + 1}`}
                onClick={() => setLines((current) => current.filter((_, j) => j !== i))}
              >
                ×
              </button>
            </div>
          ))}
          {lines.length < 200 && (
            <button
              type="button"
              onClick={() => setLines((current) => [...current, { description: "", kind: "item", amount: "" }])}
            >
              Add line
            </button>
          )}
        </fieldset>
      ) : (
        <label>
          Total
          <input inputMode="decimal" value={total} onChange={(e) => setTotal(e.target.value)} />
        </label>
      )}

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
          <button type="button" onClick={() => setDeadlines((current) => [...current, { kind: "payment_due", dueDate: "" }])}>
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
