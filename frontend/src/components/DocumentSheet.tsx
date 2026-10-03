import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { CorrectionFields, OneTallyApi, ReviewRequest } from "../api";
import { centsToInput, DEADLINE_LABELS, formatCents, formatDate, parseDollars } from "../format";
import { BillLines, type LineCheck, type LineInput } from "./BillLines";
import { BillStatus, ItemsTotal } from "./BillStatus";
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

/** Detail view as a bottom sheet: what was read, how sure we are, and fixing any of it in place. */
export function DocumentSheet({ api, docId, onClose }: DocumentSheetProps) {
  const queryClient = useQueryClient();
  const { data: doc, error, isPending } = useQuery({
    queryKey: ["document", docId],
    queryFn: () => api.getDocument(docId),
    // Still being read: keep checking so the sheet turns into the bill the moment it's done.
    refetchInterval: (query) => (isReading(query.state.data?.status) ? 1500 : false),
  });
  const [mode, setMode] = useState<"view" | "ask" | "split" | "confirm-delete">("view");

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
  // A fix made in place: the server re-adds everything (code, no AI) and says if it adds up now.
  const [lastChange, setLastChange] = useState<string | null>(null);
  const edit = useMutation({
    mutationFn: ({ fields }: { fields: CorrectionFields; change?: string }) =>
      api.reviewDocument(docId, { action: "correct", fields }),
    onMutate: () => setLastChange(null),
    onSuccess: (updated, { change }) => {
      queryClient.setQueryData(["document", docId], updated);
      for (const key of [["documents"], ["obligations"]]) queryClient.invalidateQueries({ queryKey: key });
      setLastChange(change ?? null);
    },
  });
  const checks = new Map<number, LineCheck>(
    (doc?.confidence?.checks ?? []).map((c) => [
      c.row,
      { reason: c.reason, suggestedCents: c.suggestedCents, suggestedKind: c.suggestedKind },
    ]),
  );
  const editable = doc?.status === "ready" || doc?.status === "needs_review";
  const lineProps = {
    allLines: doc?.lines ?? [],
    currency: doc?.currency ?? "USD",
    checks,
    confirming: confirm.isPending ? confirm.variables : undefined,
    onConfirm: (row: number) => confirm.mutate(row),
    saving: edit.isPending,
    onSave: (lines: LineInput[], change: string) => edit.mutate({ fields: { lines }, change }),
    editable,
  };

  const remove = useMutation({
    mutationFn: () => api.deleteDocument(docId),
    onSuccess: () => {
      for (const key of [["documents"], ["obligations"]]) queryClient.invalidateQueries({ queryKey: key });
      onClose();
    },
  });

  const currency = doc?.currency ?? "USD";
  const flagged = !!doc?.confidence?.checks.length;
  const deadlines = (doc?.obligations ?? [])
    .filter((o) => o.kind in DEADLINE_LABELS)
    .map((o) => ({ kind: o.kind, dueDate: o.dueDate }));
  const notCounted = (doc?.lines ?? []).filter((l) => l.kind === "info");
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
          <h2 id="sheet-title">
            {isReading(doc?.status) || !doc ? (
              "Reading your bill"
            ) : editable ? (
              <InlineText
                label="Vendor"
                value={doc.vendor ?? ""}
                display={doc.vendor || doc.filename || "Document"}
                saving={edit.isPending}
                onSave={(vendor) => edit.mutate({ fields: { vendor: vendor || null } })}
              />
            ) : (
              doc.vendor || doc.filename || "Document"
            )}
          </h2>
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
            {editable ? (
              <p className="sheet-meta">
                <InlineText
                  label="Date"
                  type="date"
                  value={doc.documentDate ?? ""}
                  display={doc.documentDate ? formatDate(doc.documentDate) : "Add date"}
                  saving={edit.isPending}
                  onSave={(date) => edit.mutate({ fields: { documentDate: date || null } })}
                />
                {doc.totalCents != null && " · "}
                {doc.lines?.length ? (
                  doc.totalCents != null && <strong>{formatCents(doc.totalCents, currency)}</strong>
                ) : (
                  // No lines (e.g. a warranty): the total itself is what you fix.
                  <InlineText
                    label="Total"
                    type="money"
                    value={doc.totalCents != null ? centsToInput(doc.totalCents) : ""}
                    display={<strong>{doc.totalCents != null ? formatCents(doc.totalCents, currency) : "Add total"}</strong>}
                    saving={edit.isPending}
                    validate={(total) => (parseDollars(total) === null ? "Enter the total like 26.81." : null)}
                    onSave={(total) => edit.mutate({ fields: { totalCents: parseDollars(total)! }, change: "the total" })}
                  />
                )}
              </p>
            ) : (
              (doc.documentDate || doc.totalCents != null) && (
                <p className="sheet-meta">
                  {doc.documentDate && formatDate(doc.documentDate)}
                  {doc.documentDate && doc.totalCents != null && " · "}
                  {doc.totalCents != null && <strong>{formatCents(doc.totalCents, currency)}</strong>}
                </p>
              )
            )}

            {doc.status === "failed" && <p className="notice notice-error">{doc.failureReason}</p>}

            {mode === "view" && (
              <BillStatus
                math={doc.math}
                confidence={doc.confidence}
                issues={doc.issues ?? []}
                currency={currency}
                lastChange={lastChange}
              />
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
            ) : (
              <>
                <BillLines
                  label="Items"
                  lines={(doc.lines ?? []).filter((l) => ITEM_KINDS.has(l.kind))}
                  addKind={doc.lines?.length ? "item" : undefined}
                  footer={<ItemsTotal math={doc.math} currency={currency} />}
                  {...lineProps}
                />
                <BillLines
                  label="Summary"
                  lines={(doc.lines ?? []).filter((l) => SUMMARY_KINDS.has(l.kind))}
                  showKind
                  {...lineProps}
                />
                {!!notCounted.length && (
                  <BillLines
                    label="Not counted in the total"
                    lines={notCounted}
                    showKind
                    {...lineProps}
                    footer={
                      <p className="muted not-counted-note">
                        Notes and crossed-out prices. If one is really part of the bill, tap it and change it to an item.
                      </p>
                    }
                  />
                )}
                {doc.taxIncluded && (
                  <p className="muted tax-note">
                    Prices include tax
                    {doc.taxCents ? ` (${formatCents(doc.taxCents, currency)} of the total)` : ""}.
                  </p>
                )}
                {editable ? (
                  (!!deadlines.length || doc.docType !== "receipt") && (
                    <Deadlines deadlines={deadlines} saving={edit.isPending} onSave={(d) => edit.mutate({ fields: { deadlines: d } })} />
                  )
                ) : (
                  !!deadlines.length && (
                    <div className="sheet-section">
                      <h3>Deadlines</h3>
                      <ul className="plain-list">
                        {deadlines.map((o) => (
                          <li key={`${o.kind}-${o.dueDate}`}>
                            {DEADLINE_LABELS[o.kind] ?? o.kind}: {formatDate(o.dueDate)}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )
                )}
                {editable && (doc.status === "needs_review" || flagged) && (
                  <button
                    type="button"
                    className="btn-block soft"
                    disabled={review.isPending}
                    onClick={() => review.mutate({ action: "accept" })}
                  >
                    ✓ Everything looks right
                  </button>
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
            {[review.error, edit.error, confirm.error].filter(Boolean).map((e) => (
              <p key={e!.message} role="alert" className="error">
                {e!.message}
              </p>
            ))}
          </>
        )}
      </section>
    </div>
  );
}


function InlineText({
  label,
  value,
  display,
  type = "text",
  saving,
  onSave,
  validate,
}: {
  label: string;
  value: string;
  display: React.ReactNode;
  type?: "text" | "date" | "money";
  saving: boolean;
  onSave: (value: string) => void;
  /** A message when the value can't be saved (e.g. "Enter the total like 26.81"). */
  validate?: (value: string) => string | null;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [problem, setProblem] = useState<string | null>(null);
  if (!editing) {
    return (
      <button
        type="button"
        className="line-tap inline-tap"
        aria-label={`Edit ${label.toLowerCase()}`}
        onClick={() => {
          setDraft(value);
          setEditing(true);
        }}
      >
        {display}
      </button>
    );
  }
  const finish = () => {
    const error = draft.trim() !== value && validate ? validate(draft.trim()) : null;
    setProblem(error);
    if (error) return;
    setEditing(false);
    if (draft.trim() !== value) onSave(draft.trim());
  };
  return (
    <form
      className="inline-form"
      onSubmit={(e) => {
        e.preventDefault();
        finish();
      }}
    >
      <input
        aria-label={label}
        type={type === "date" ? "date" : "text"}
        inputMode={type === "money" ? "decimal" : undefined}
        value={draft}
        autoFocus
        maxLength={200}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={finish}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.stopPropagation();
            setEditing(false);
          }
        }}
        disabled={saving}
      />
      {problem && (
        <span role="alert" className="error inline-error">
          {problem}
        </span>
      )}
    </form>
  );
}

interface DeadlineDraft {
  kind: string;
  dueDate: string;
}

/** Deadlines you fix in place: tap one to change it, or add one. */
function Deadlines({
  deadlines,
  saving,
  onSave,
}: {
  deadlines: DeadlineDraft[];
  saving: boolean;
  onSave: (deadlines: DeadlineDraft[]) => void;
}) {
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<DeadlineDraft>({ kind: "payment_due", dueDate: "" });
  const open = (i: number) => {
    setDraft(i < deadlines.length ? deadlines[i] : { kind: "payment_due", dueDate: "" });
    setEditing(i);
  };
  const save = (next: DeadlineDraft[]) => {
    onSave(next);
    setEditing(null);
  };
  const editor = (i: number) => (
    <form
      className="line-editor"
      onSubmit={(e) => {
        e.preventDefault();
        if (!draft.dueDate) return;
        save(i < deadlines.length ? deadlines.map((d, j) => (j === i ? draft : d)) : [...deadlines, draft]);
      }}
    >
      <select aria-label="Deadline type" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>
        {Object.entries(DEADLINE_LABELS).map(([value, name]) => (
          <option key={value} value={value}>
            {name}
          </option>
        ))}
      </select>
      <input
        type="date"
        aria-label="Deadline date"
        value={draft.dueDate}
        onChange={(e) => setDraft({ ...draft, dueDate: e.target.value })}
      />
      <div className="line-editor-actions">
        <button type="submit" className="primary" disabled={saving || !draft.dueDate}>
          Save
        </button>
        <button type="button" onClick={() => setEditing(null)}>
          Cancel
        </button>
        {i < deadlines.length && (
          <button
            type="button"
            className="link-button danger"
            onClick={() => save(deadlines.filter((_, j) => j !== i))}
          >
            Remove
          </button>
        )}
      </div>
    </form>
  );

  return (
    <div className="sheet-section">
      <h3>Deadlines</h3>
      <ul className="plain-list">
        {deadlines.map((d, i) => (
          <li key={`${d.kind}-${d.dueDate}-${i}`}>
            {editing === i ? (
              editor(i)
            ) : (
              <button type="button" className="line-tap" onClick={() => open(i)}>
                {DEADLINE_LABELS[d.kind] ?? d.kind}: {formatDate(d.dueDate)}
              </button>
            )}
          </li>
        ))}
      </ul>
      {editing === deadlines.length ? (
        editor(deadlines.length)
      ) : (
        deadlines.length < 10 && (
          <button type="button" className="link-button add-line" onClick={() => open(deadlines.length)}>
            + Add deadline
          </button>
        )
      )}
    </div>
  );
}
