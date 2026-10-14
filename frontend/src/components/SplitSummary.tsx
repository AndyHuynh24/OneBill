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
  /** Tick or untick someone as having paid you back (hidden when not given). */
  onTogglePaid?: (member: string, paid: boolean) => void;
}

/** Who owes what. The shares come from the server and always add up to the receipt. */
export function SplitSummary({ split, doc, onEdit, onDone, onAddPeople, onTogglePaid }: SplitSummaryProps) {
  const [copied, setCopied] = useState(false);
  const currency = doc.currency ?? "USD";
  const description = new Map((doc.lines ?? []).map((l) => [l.row, l.description]));
  const sum = split.shares.reduce((acc, s) => acc + s.totalCents, 0);
  const isPaid = (member: string) => !!split.paid?.[member];
  const self = new Set(split.members.filter((m) => m.self).map((m) => m.id));
  const owing = split.shares.filter((s) => !self.has(s.member));
  const owedTotal = owing.reduce((acc, s) => acc + s.totalCents, 0);
  const paidBack = owing.filter((s) => isPaid(s.member)).reduce((acc, s) => acc + s.totalCents, 0);

  async function share(only?: string) {
    const text = shareText(split, doc.lines ?? [], doc, only);
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
          const paid = isPaid(s.member);
          const owes = !!onTogglePaid && !member?.self;
          return (
            <li key={s.member} className={paid ? "share-row share-paid" : "share-row"} aria-label={member?.name}>
              <Avatar name={member?.name ?? "?"} seat={seat} size="sm" />
              <div className="share-detail">
                <strong>{member?.name}</strong>
                <span className="muted">
                  {items.join(", ") || "No items"}
                  {s.extrasCents ? ` + ${formatCents(s.extrasCents, currency)} tax/tip/fees` : ""}
                </span>
                {owes && !paid && (
                  <button type="button" className="link-button remind" onClick={() => share(s.member)}>
                    Remind {member?.name}
                  </button>
                )}
              </div>
              <div className="share-amount">
                <strong className="share-total">{formatCents(s.totalCents, currency)}</strong>
                {paid && <span className="paid-label">Paid</span>}
              </div>
              {owes && (
                <button
                  type="button"
                  className="paid-toggle"
                  aria-pressed={paid}
                  aria-label={`Mark ${member?.name} as ${paid ? "not paid" : "paid"}`}
                  onClick={() => onTogglePaid(s.member, !paid)}
                >
                  {paid ? "✓" : ""}
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {onAddPeople && (
        <button type="button" className="add-people" onClick={onAddPeople}>
          + Add more people and split again
        </button>
      )}
      {onTogglePaid && owedTotal > 0 && (
        <p className="paid-progress">
          {paidBack === owedTotal
            ? "Everyone has paid ✓"
            : `${formatCents(paidBack, currency)} of ${formatCents(owedTotal, currency)} paid back`}
        </p>
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
        <button type="button" className="primary" onClick={() => share()}>
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
