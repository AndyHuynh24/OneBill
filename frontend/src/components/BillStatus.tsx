import type { BillMath, Confidence } from "../api";
import { formatCents } from "../format";

/**
 * The one message at the top of a bill: does it add up, and if not, exactly where it's off,
 * in the numbers the server checked. After a fix, it says what changed and how that settled it.
 */
export function BillStatus({
  math,
  confidence,
  issues,
  currency,
  lastChange,
}: {
  math?: BillMath;
  confidence?: Confidence;
  issues: string[];
  currency: string;
  /** "Tax: $16.61 → $16.51", after a fix made on this screen. */
  lastChange?: string | null;
}) {
  const money = (cents: number) => formatCents(cents, currency);
  const flagged = confidence?.checks.length ?? 0;

  if (!math) {
    // Bills read before this screen existed: just list what's off, if anything.
    return issues.length ? (
      <div className="bill-status warn">
        <strong>Worth a look</strong>
        <ul>
          {issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      </div>
    ) : null;
  }

  const itemsOff = math.itemsMatch === false && math.subtotalCents != null;
  const totalOff = math.totalMatch === false && math.totalCents != null;
  const others = issues.filter(
    (i) =>
      !i.startsWith("Items add up to") &&
      !i.includes("but the receipt's total is"),
  );
  const discount = math.discountAfterCents ? ` − discount ${money(-math.discountAfterCents)}` : "";
  const charges = [
    math.taxCents ? `tax ${money(math.taxCents)}` : null,
    math.tipCents ? `tip ${money(math.tipCents)}` : null,
    math.feesCents ? `fees ${money(math.feesCents)}` : null,
  ].filter(Boolean);
  const totalSum = `${math.subtotalCents != null ? "Subtotal" : "Items"} ${money(math.basisCents)}${discount}${charges.map((c) => ` + ${c}`).join("")}`;

  let tone: "ok" | "warn";
  let headline: string;
  const details: string[] = [];
  if (itemsOff || totalOff) {
    tone = "warn";
    const gaps = [
      itemsOff ? Math.abs(math.itemsCents - math.subtotalCents!) : 0,
      totalOff ? Math.abs(math.expectedCents - math.totalCents!) : 0,
    ];
    headline = `Off by ${money(Math.max(...gaps))}`;
    if (itemsOff) {
      details.push(
        `The items add up to ${money(math.itemsCents)}, but the receipt's subtotal is ${money(math.subtotalCents!)}.`,
      );
    }
    if (totalOff) {
      details.push(
        `${totalSum} = ${money(math.expectedCents)}, but the receipt's total is ${money(math.totalCents!)}.`,
      );
    }
    details.push(
      flagged
        ? "Check the yellow lines, or tap any line to fix it."
        : "Tap any line to fix it.",
    );
  } else if (flagged) {
    tone = "warn";
    headline = `It adds up, but ${flagged === 1 ? "1 amount" : `${flagged} amounts`} may be misread`;
    details.push(
      "Check the yellow lines: tap ✓ if it's right, or tap the line to fix it.",
    );
  } else {
    // All good: just how sure the reading is. The math only shows when something's off.
    tone = "ok";
    const sure = {
      confident: ["High confidence", "Every amount was read clearly, found on the receipt, and adds up."],
      mostly: ["Good confidence", "It adds up. A quick glance is still worth it."],
      confirmed: ["Checked by you", "You confirmed or fixed the flagged amounts, and it adds up."],
    }[confidence?.level as "confident" | "mostly" | "confirmed"] ?? ["Looks good", "The amounts add up."];
    headline = sure[0];
    details.push(sure[1]);
  }

  return (
    <div className={`bill-status ${tone}`} role="status">
      {lastChange && <p className="bill-change">You changed {lastChange}</p>}
      <div className="bill-head">
        {tone === "ok" && <span className="confidence-dot" aria-hidden="true" />}
        <strong className="bill-headline">{headline}</strong>
      </div>
      {details.map((d) => (
        <p key={d}>{d}</p>
      ))}
      {!!others.length && (
        <ul className="bill-others">
          {others.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Under the items: their sum, calculated here from the lines, next to the printed subtotal. */
export function ItemsTotal({
  math,
  currency,
}: {
  math?: BillMath;
  currency: string;
}) {
  if (!math) return null;
  const money = (cents: number) => formatCents(cents, currency);
  const printed = math.subtotalCents;
  // Adds up: one quiet line. Off: the full comparison.
  if (math.itemsMatch !== false) {
    return (
      <p className="items-total-ok">
        Items add up to <strong>{money(math.itemsCents)}</strong>
        {printed != null && <span className="items-ok-mark"> ✓ matches the receipt</span>}
      </p>
    );
  }
  return (
    <div className={`items-total ${math.itemsMatch === false ? "off" : ""}`}>
      <span>
        Sum of the items above <span className="calc-tag">calculated</span>
      </span>
      <strong>{money(math.itemsCents)}</strong>
      {printed != null && (
        <span className="items-compare">
          {math.itemsMatch === false
            ? `✗ Receipt subtotal is ${money(printed)}: off by ${money(Math.abs(math.itemsCents - printed))}`
            : math.taxIncluded || math.feesInSubtotal
              ? `✓ Matches the receipt (${money(printed)} subtotal)`
              : "✓ Matches the receipt's subtotal"}
        </span>
      )}
    </div>
  );
}
