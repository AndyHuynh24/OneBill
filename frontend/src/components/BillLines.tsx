import { useState } from "react";
import type { DocumentLine, LineKind } from "../api";
import { centsToInput, formatCents, parseSignedDollars } from "../format";

/** A line as the API takes it when lines are corrected (rows are numbered by the server). */
export interface LineInput {
  description: string;
  kind: LineKind;
  amountCents: number;
}

export interface LineCheck {
  reason: string;
  suggestedCents?: number;
  /** "item": a line left out of the math that would make it add up if counted. */
  suggestedKind?: LineKind;
}

/** Every kind a line can be, in the words people use. */
/** "TAX" already says Tax: the kind label only shows when it adds something ("VISA ****1234 Paid"). */
const saysKind = (line: DocumentLine) =>
  line.description.toLowerCase().includes(LINE_KIND_LABELS[line.kind].toLowerCase());

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
  info: "Not counted",
};

const asInput = ({
  description,
  kind,
  amountCents,
}: DocumentLine): LineInput => ({ description, kind, amountCents });

interface BillLinesProps {
  label: string;
  /** The lines this table shows. */
  lines: DocumentLine[];
  /** Every line of the bill, in order: a save sends them all, with this one change. */
  allLines: DocumentLine[];
  currency: string;
  showKind?: boolean;
  checks: Map<number, LineCheck>;
  /** Row being confirmed with ✓ Looks right. */
  confirming?: number;
  onConfirm: (row: number) => void;
  saving: boolean;
  /** `change` says what was fixed, in words ("Tax: $16.61 → $16.51"). */
  onSave: (lines: LineInput[], change: string) => void;
  editable: boolean;
  /** "Add item" under the table, adding a line of this kind. */
  addKind?: LineKind;
  /** Shown right under the table (e.g. the calculated sum of the items). */
  footer?: React.ReactNode;
}

/**
 * A table of bill lines you fix in place: tap a line to edit its name, amount, or kind; Enter
 * saves. The server re-adds everything (code, no AI) and says whether it adds up now. Flagged
 * lines are highlighted with ✓ Looks right, and "Use $16.51" when one digit would fix the totals.
 */
export function BillLines({
  label,
  lines,
  allLines,
  currency,
  showKind = false,
  checks,
  confirming,
  onConfirm,
  saving,
  onSave,
  editable,
  addKind,
  footer,
}: BillLinesProps) {
  const [editing, setEditing] = useState<number | "new" | null>(null);
  if (!lines.length && !addKind) return null;

  const money = (cents: number) => formatCents(cents, currency);
  const save = (next: LineInput[], change: string) => {
    onSave(next, change);
    setEditing(null);
  };
  const describe = (before: DocumentLine, after: LineInput | null): string => {
    if (!after) return `removed ${before.description}`;
    const parts = [];
    if (after.kind !== before.kind) parts.push(`counted as ${LINE_KIND_LABELS[after.kind].toLowerCase()}`);
    if (after.amountCents !== before.amountCents) parts.push(`${money(before.amountCents)} → ${money(after.amountCents)}`);
    if (after.description !== before.description) parts.push(`renamed to ${after.description}`);
    return `${before.description}: ${parts.join(", ")}`;
  };
  const replaceRow = (row: number, line: LineInput | null) => {
    const before = allLines.find((l) => l.row === row)!;
    save(
      allLines.flatMap((l) => (l.row === row ? (line ? [line] : []) : [asInput(l)])),
      describe(before, line),
    );
  };
  const addLine = (line: LineInput) => {
    // Right after this table's last line, so new items land above the subtotal.
    const after = lines.length ? lines[lines.length - 1].row : allLines.length;
    const next = allLines.flatMap((l) => (l.row === after ? [asInput(l), line] : [asInput(l)]));
    save(lines.length ? next : [...allLines.map(asInput), line], `added ${line.description} ${money(line.amountCents)}`);
  };

  return (
    <div className="sheet-section">
      <h3>{label}</h3>
      {!!lines.length && (
        <table className="lines" aria-label={label}>
          <tbody>
            {lines.map((line) => {
              const check = checks.get(line.row);
              if (editing === line.row) {
                return (
                  <tr key={line.row} className="line-editing">
                    <td colSpan={2}>
                      <LineEditor
                        initial={asInput(line)}
                        saving={saving}
                        onSave={(edited) => replaceRow(line.row, edited)}
                        onRemove={() => replaceRow(line.row, null)}
                        onCancel={() => setEditing(null)}
                      />
                    </td>
                  </tr>
                );
              }
              return (
                <tr key={line.row} className={check ? "line-check" : undefined}>
                  <td>
                    {editable ? (
                      <button
                        type="button"
                        className="line-tap"
                        aria-label={`Edit ${line.description}`}
                        onClick={() => setEditing(line.row)}
                      >
                        {line.description}
                        {showKind && !saysKind(line) && (
                          <span className="kind">
                            {" "}
                            {LINE_KIND_LABELS[line.kind]}
                          </span>
                        )}
                        {!showKind && line.kind === "info" && <span className="tag-not-counted">not counted yet</span>}
                      </button>
                    ) : (
                      <>
                        {line.description}
                        {showKind && !saysKind(line) && (
                          <span className="kind">
                            {" "}
                            {LINE_KIND_LABELS[line.kind]}
                          </span>
                        )}
                      </>
                    )}
                    {check && (
                      <span className="check-row">
                        <span className="check-reason">{check.reason}</span>
                        {check.suggestedCents != null && editable && (
                          <button
                            type="button"
                            className="check-use"
                            disabled={saving}
                            onClick={() =>
                              replaceRow(line.row, {
                                ...asInput(line),
                                amountCents: check.suggestedCents!,
                                kind: check.suggestedKind ?? line.kind,
                              })
                            }
                          >
                            {check.suggestedKind === "item"
                              ? `Count as item: ${money(check.suggestedCents)}`
                              : `Use ${money(check.suggestedCents)}`}
                          </button>
                        )}
                        <button
                          type="button"
                          className="check-ok"
                          aria-label={`${check.suggestedKind === "item" ? "Leave it out" : "Looks right"}: ${line.description} ${formatCents(line.amountCents, currency)}`}
                          disabled={confirming !== undefined}
                          onClick={() => onConfirm(line.row)}
                        >
                          {confirming === line.row
                            ? "Saving…"
                            : check.suggestedKind === "item"
                              ? "✓ Leave it out"
                              : "✓ Looks right"}
                        </button>
                      </span>
                    )}
                  </td>
                  <td className="amount">
                    {editable ? (
                      <button
                        type="button"
                        className="line-tap amount-tap"
                        aria-label={`Edit amount of ${line.description}`}
                        onClick={() => setEditing(line.row)}
                      >
                        {formatCents(line.amountCents, currency)}
                      </button>
                    ) : (
                      formatCents(line.amountCents, currency)
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {footer}
      {editable && addKind && editing === "new" && (
        <div className="line-editing-new">
          <LineEditor
            initial={{ description: "", kind: addKind, amountCents: 0 }}
            fresh
            saving={saving}
            onSave={addLine}
            onCancel={() => setEditing(null)}
          />
        </div>
      )}
      {editable && addKind && editing !== "new" && (
        <button
          type="button"
          className="link-button add-line"
          onClick={() => setEditing("new")}
        >
          + Add {LINE_KIND_LABELS[addKind].toLowerCase()}
        </button>
      )}
    </div>
  );
}

function LineEditor({
  initial,
  fresh = false,
  saving,
  onSave,
  onRemove,
  onCancel,
}: {
  initial: LineInput;
  fresh?: boolean;
  saving: boolean;
  onSave: (line: LineInput) => void;
  onRemove?: () => void;
  onCancel: () => void;
}) {
  const [description, setDescription] = useState(initial.description);
  const [amount, setAmount] = useState(
    fresh ? "" : centsToInput(initial.amountCents),
  );
  const [kind, setKind] = useState<LineKind>(initial.kind);
  const [problem, setProblem] = useState<string | null>(null);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const cents = parseSignedDollars(amount);
    if (!description.trim()) return setProblem("Add a name.");
    if (cents === null) return setProblem("Enter an amount like 4.19.");
    const edited = {
      description: description.trim(),
      kind,
      amountCents: cents,
    };
    if (!fresh && JSON.stringify(edited) === JSON.stringify(initial))
      return onCancel();
    onSave(edited);
  }

  return (
    <form
      className="line-editor"
      onSubmit={submit}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation(); // close the editor, not the whole sheet
          onCancel();
        }
      }}
    >
      <input
        aria-label="Name"
        value={description}
        maxLength={120}
        autoFocus={fresh}
        onChange={(e) => setDescription(e.target.value)}
      />
      <input
        aria-label="Amount"
        className="line-amount"
        inputMode="decimal"
        value={amount}
        autoFocus={!fresh}
        onFocus={(e) => e.target.select()}
        onChange={(e) => setAmount(e.target.value)}
      />
      <select
        aria-label="Kind"
        value={kind}
        onChange={(e) => setKind(e.target.value as LineKind)}
      >
        {Object.entries(LINE_KIND_LABELS).map(([value, name]) => (
          <option key={value} value={value}>
            {name}
          </option>
        ))}
      </select>
      <div className="line-editor-actions">
        <button type="submit" className="primary" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        {onRemove && (
          <button
            type="button"
            className="link-button danger"
            onClick={onRemove}
            disabled={saving}
          >
            Remove line
          </button>
        )}
      </div>
      {problem && (
        <p role="alert" className="error">
          {problem}
        </p>
      )}
    </form>
  );
}
