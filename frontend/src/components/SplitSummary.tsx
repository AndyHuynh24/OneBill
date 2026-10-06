import { useState } from "react";
import type { DocumentDetail, Split } from "../api";
import { formatCents } from "../format";
import { shareText } from "../split";
import { Avatar } from "./Avatar";

interface SplitSummaryProps {
  split: Split;
  doc: DocumentDetail;
  onEdit: () => void;
  onDone: () => void;
  /** Back to "Who's here?" with everyone kept, to add people and split again. */
  onAddPeople?: () => void;
}

/** Who owes what. The shares come from the server and always add up to the receipt. */
export function SplitSummary({ split, doc, onEdit, onDone, onAddPeople }: SplitSummaryProps) {
  const [copied, setCopied] = useState(false);
  const currency = doc.currency ?? "USD";
  const description = new Map((doc.lines ?? []).map((l) => [l.row, l.description]));
  const sum = split.shares.reduce((acc, s) => acc + s.totalCents, 0);

  async function share() {
    const text = shareText(split, doc.lines ?? [], doc);
    if (typeof navigator.share === "function") {
      await navigator.share({ text }).catch(() => undefined); // closing the sheet isn't an error
    } else {
      await navigator.clipboard?.writeText(text);
      setCopied(true);
    }
  }

  return (
    <div className="split-summary">
      <h2>Who owes what</h2>
      <ul className="plain-list">
        {split.shares.map((s) => {
          const seat = split.members.findIndex((m) => m.id === s.member);
          const member = split.members[seat];
          const items = s.rows.map((r) => description.get(r)).filter(Boolean);
          return (
            <li key={s.member} className="share-row" aria-label={member?.name}>
              <Avatar name={member?.name ?? "?"} seat={seat} size="sm" />
              <div className="share-detail">
                <strong>{member?.name}</strong>
                <span className="muted">
                  {items.join(", ") || "No items"}
                  {s.extrasCents ? ` + ${formatCents(s.extrasCents, currency)} tax/tip/fees` : ""}
                </span>
              </div>
              <strong className="share-total">{formatCents(s.totalCents, currency)}</strong>
            </li>
          );
        })}
      </ul>
      {onAddPeople && (
        <button type="button" className="add-people" onClick={onAddPeople}>
          + Add more people and split again
        </button>
      )}
      <p className={sum === split.totalCents ? "split-check" : "split-check error"}>
        {sum === split.totalCents
          ? `Adds up to ${formatCents(split.totalCents, currency)} ✓`
          : `Shares total ${formatCents(sum, currency)}, receipt says ${formatCents(split.totalCents, currency)}`}
      </p>
      {split.matchesReceipt === false && split.receiptTotalCents != null && (
        <p className="notice notice-warn">
          Split on the recorded items ({formatCents(split.totalCents, currency)}). The receipt says{" "}
          {formatCents(split.receiptTotalCents, currency)}; fix the bill and split again if a number was misread.
        </p>
      )}
      {split.taxIncluded && <p className="muted tax-note">Prices include tax, so it's already in each share.</p>}
      <div className="sheet-actions">
        <button type="button" className="primary" onClick={share}>
          Share
        </button>
        <button type="button" onClick={onEdit}>
          Edit
        </button>
        <button type="button" onClick={onDone}>
          Done
        </button>
      </div>
      {copied && (
        <p role="status" className="muted">
          Copied. Paste it into your group chat.
        </p>
      )}
    </div>
  );
}
