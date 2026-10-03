"""Code-only arithmetic for receipts and bills.

The model *labels* each money line with a kind; this module does all the math and checks it
against every printed number. Labels are treated as suggestions: summary, payment, and change
lines that were mislabelled as items are caught here, so a "TOTAL" line can never be summed
into the total.
"""

import re
from dataclasses import dataclass, field, replace
from typing import Literal

from shared.money import format_cents

LineKind = Literal[
    "item",
    "discount",
    "fee",
    "tax",
    "tip",
    "subtotal",
    "total",
    "payment",
    "change",
    "balance_due",
    "info",
]

TOLERANCE_CENTS = 2
SUMMABLE: frozenset[str] = frozenset({"item", "discount", "fee"})

# Checked in order; descriptions are normalized to upper-case words first.
# "Recap" lines (savings totals) are checked before "total" so "TOTAL SAVINGS" isn't a total.
_KEYWORD_KINDS: tuple[tuple[re.Pattern[str], LineKind], ...] = (
    (re.compile(r"^(YOU SAVED|YOUR SAVINGS|TOTAL SAVINGS|SAVINGS)\b"), "info"),
    (re.compile(r"^SUB ?TOTAL\b"), "subtotal"),
    (re.compile(r"^(BALANCE( DUE)?|AMOUNT DUE|AMT DUE|TOTAL DUE)\b"), "balance_due"),
    (re.compile(r"^(GRAND )?TOTAL\b"), "total"),
    (re.compile(r"^((SALES )?TAX|HST|GST|PST|VAT)\b|\bTAX$"), "tax"),
    (re.compile(r"^(TIP|GRATUITY)\b"), "tip"),
    (re.compile(r"^CHANGE( DUE)?\b"), "change"),
    (
        re.compile(
            r"^(CASH|TEND|TENDER|TENDERED|AMOUNT TENDERED|VISA|MASTERCARD|MC|AMEX|"
            r"AMERICAN EXPRESS|DISCOVER|DEBIT|CREDIT|CREDIT CARD|CARD|GIFT CARD|EBT|"
            r"APPLE PAY|GOOGLE PAY)\b"
        ),
        "payment",
    ),
)


@dataclass(frozen=True)
class Line:
    row: int
    description: str
    kind: LineKind
    amount_cents: int


@dataclass(frozen=True)
class Correction:
    row: int
    description: str
    from_kind: LineKind
    to_kind: LineKind
    reason: str


@dataclass
class Reconciliation:
    lines: list[Line]
    corrections: list[Correction] = field(default_factory=list)
    items_cents: int = 0
    discounts_cents: int = 0
    fees_cents: int = 0
    tax_cents: int = 0
    tip_cents: int = 0
    payment_cents: int = 0
    change_cents: int = 0
    printed_subtotal_cents: int | None = None
    total_cents: int | None = None  # always a *printed* total, never a computed one
    issues: list[str] = field(default_factory=list)
    # Oddities that don't affect the bill's arithmetic (e.g. a confusing payment section).
    notes: list[str] = field(default_factory=list)
    suspect_rows: list[int] = field(default_factory=list)
    total_ok: bool | None = None  # None when there is no total to check
    # Prices already include tax (common outside the US): items add up to the total, and the
    # printed tax is the part of it that's tax. Splitting must not add the tax again.
    tax_included: bool = False
    overage_cents: int = 0  # computed − printed total, when the total check fails

    @property
    def ok(self) -> bool:
        return not self.issues


def reconcile(
    lines: list[Line],
    textract_summary: dict[str, int] | None = None,
    total_required: bool = True,
    savings: set[int] | None = None,
) -> Reconciliation:
    """Check a labelled receipt, correcting mislabelled summary lines when the math proves it.

    1. Check the labels as given. If they add up, trust them.
    2. Otherwise, relabel every summable line whose text names a summary/payment line
       (TOTAL, CASH, CHANGE, …). Accept that if it makes everything add up.
    3. Otherwise, relabel only the lines with direct evidence (their amount is a printed
       summary value, or equals the running sum above them), and report what still fails.
    """
    textract_values = set((textract_summary or {}).values())
    normalized = [_normalize_discount(line) for line in lines]
    normalized, originals = _crossed_out_originals(normalized, savings or set())
    result = _reconcile(normalized, textract_values, total_required)
    result.corrections = originals + result.corrections
    return result


def _crossed_out_originals(
    lines: list[Line], savings: set[int]
) -> tuple[list[Line], list[Correction]]:
    """Online orders print a sale price, the crossed-out original, and "$1.60 from savings". If
    the same product appears twice and the two prices differ by exactly a printed saving, the
    higher one is the crossed-out original: relabel it info. (The only rule that touches items,
    and only with that printed evidence.)"""
    if not savings:
        return lines, []
    groups: dict[str, list[Line]] = {}
    for line in lines:
        if line.kind == "item":
            groups.setdefault(_normalize(line.description), []).append(line)
    relabel: dict[int, LineKind] = {}
    for group in groups.values():
        used: set[int] = set()
        for high in sorted(group, key=lambda line: line.amount_cents, reverse=True):
            for low in group:
                if {high.row, low.row} & used or low.amount_cents >= high.amount_cents:
                    continue
                if any(_close(high.amount_cents - low.amount_cents, s) for s in savings):
                    relabel[high.row] = "info"
                    used |= {high.row, low.row}
                    break
    if not relabel:
        return lines, []
    return _apply(lines, relabel, "crossed-out original price (its saving is printed)")


def _reconcile(
    normalized: list[Line], textract_values: set[int], total_required: bool
) -> Reconciliation:
    as_given = _check(normalized, total_required)
    if as_given.ok:
        return as_given

    candidates = _keyword_candidates(normalized)
    if candidates:
        relabelled, corrections = _apply(
            normalized, candidates, "text names a summary or payment line"
        )
        hypothesis = _with_overage_rule(relabelled, corrections, total_required)
        if hypothesis.ok:
            return hypothesis

    evidence = _evidence_candidates(normalized, candidates, textract_values)
    current, corrections = (
        _apply(normalized, evidence, "amount matches a printed summary value")
        if evidence
        else (normalized, [])
    )
    result = _with_overage_rule(current, corrections, total_required)
    result.suspect_rows = _suspect_rows(result, textract_values)
    return result


# Summary lines that a recap or a repeated charge can duplicate. Items are never touched.
_RECAP_KINDS = frozenset({"tax", "tip", "fee"})
_MAX_RECAP_CANDIDATES = 10


def _with_overage_rule(
    lines: list[Line], corrections: list[Correction], total_required: bool
) -> Reconciliation:
    """If the computed total is too high by exactly the sum of some tax/tip/fee lines, those
    lines repeat amounts already counted (a tax breakdown after the total, a gratuity printed
    twice): relabel them as info.

    Among the subsets that fix the total, prefer lines printed after the final total (recaps),
    then later lines (the repeat rather than the first mention).
    """
    result = _check(lines, total_required)
    result.corrections = list(corrections)
    if result.total_ok is not False or result.overage_cents <= 0:
        return result
    candidates = [line for line in lines if line.kind in _RECAP_KINDS]
    if not candidates or len(candidates) > _MAX_RECAP_CANDIDATES:
        return result
    last_total_row = max((line.row for line in lines if line.kind == "total"), default=0)

    best: tuple[tuple, list[Line]] | None = None
    for subset in _subsets(candidates):
        if not _close(sum(line.amount_cents for line in subset), result.overage_cents):
            continue
        rows = sorted((line.row for line in subset), reverse=True)
        preference = (sum(1 for row in rows if row > last_total_row), rows)
        if best is None or preference > best[0]:
            best = (preference, subset)
    if best is None:
        return result

    relabel = {line.row: "info" for line in best[1]}
    fixed_lines, extra = _apply(
        lines, relabel, "repeats an amount already counted (recap or duplicate)"
    )
    fixed = _check(fixed_lines, total_required)
    if fixed.total_ok is not True:
        return result
    fixed.corrections = list(corrections) + extra
    return fixed


def _subsets(items: list[Line]):
    for mask in range(1, 1 << len(items)):
        yield [item for i, item in enumerate(items) if mask >> i & 1]


def check(lines: list[Line], total_required: bool = True) -> Reconciliation:
    """Check lines exactly as labelled, without relabelling: for a person's own corrections."""
    return _check([_normalize_discount(line) for line in lines], total_required)


def _normalize(description: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", description.upper()).split())


def _keyword_kind(description: str) -> LineKind | None:
    text = _normalize(description)
    for pattern, kind in _KEYWORD_KINDS:
        if pattern.search(text):
            return kind
    return None


def _normalize_discount(line: Line) -> Line:
    if line.kind == "discount" and line.amount_cents > 0:
        return replace(line, amount_cents=-line.amount_cents)
    return line


def _keyword_candidates(lines: list[Line]) -> dict[int, LineKind]:
    found: dict[int, LineKind] = {}
    for line in lines:
        if line.kind in SUMMABLE:
            kind = _keyword_kind(line.description)
            if kind is not None and kind != line.kind:
                found[line.row] = kind
    return found


def _evidence_candidates(
    lines: list[Line], keyword: dict[int, LineKind], textract_values: set[int]
) -> dict[int, LineKind]:
    printed = _printed_values(lines) | textract_values
    found: dict[int, LineKind] = {}
    running = 0
    for line in lines:
        if line.row in keyword:
            amount = abs(line.amount_cents)
            if any(_close(amount, p) for p in printed) or _close(amount, running):
                found[line.row] = keyword[line.row]
                continue
        if line.kind in SUMMABLE:
            running += line.amount_cents
    return found


def _apply(
    lines: list[Line], relabel: dict[int, LineKind], reason: str
) -> tuple[list[Line], list[Correction]]:
    out: list[Line] = []
    corrections: list[Correction] = []
    for line in lines:
        new_kind = relabel.get(line.row)
        if new_kind is None:
            out.append(line)
            continue
        amount = abs(line.amount_cents)
        out.append(replace(line, kind=new_kind, amount_cents=amount))
        corrections.append(Correction(line.row, line.description, line.kind, new_kind, reason))
    return out, corrections


def _printed_values(lines: list[Line]) -> set[int]:
    return {
        abs(line.amount_cents)
        for line in lines
        if line.kind not in SUMMABLE and line.kind != "info"
    }


def _close(a: int, b: int) -> bool:
    return abs(a - b) <= TOLERANCE_CENTS


def _sum(lines: list[Line], kind: str) -> int:
    return sum(line.amount_cents for line in lines if line.kind == kind)


def _distinct(values: list[int]) -> list[int]:
    out: list[int] = []
    for value in values:
        if not any(_close(value, seen) for seen in out):
            out.append(value)
    return out


def _check(lines: list[Line], total_required: bool = True) -> Reconciliation:
    r = Reconciliation(
        lines=lines,
        items_cents=_sum(lines, "item"),
        discounts_cents=_sum(lines, "discount"),
        fees_cents=_sum(lines, "fee"),
        tax_cents=_sum(lines, "tax"),
        tip_cents=_sum(lines, "tip"),
        payment_cents=_sum(lines, "payment"),
        change_cents=_sum(lines, "change"),
    )
    goods = r.items_cents + r.discounts_cents
    totals = [line.amount_cents for line in lines if line.kind == "total"]
    if not totals:
        totals = [line.amount_cents for line in lines if line.kind == "balance_due"]
    subtotals = _distinct([line.amount_cents for line in lines if line.kind == "subtotal"])

    # Tax-inclusive pricing: the items already add up to the total, and (when a pre-tax
    # subtotal is printed) subtotal + tax gives the same total.
    if r.tax_cents and totals:
        extras = r.tip_cents + r.fees_cents
        r.tax_included = _close(goods + extras, totals[-1]) and (
            not subtotals or _close(subtotals[-1] + r.tax_cents + extras, totals[-1])
        )

    # 1. items (+ discounts, maybe + fees) ≈ printed subtotal
    fees_in_subtotal = False
    if len(subtotals) > 1:
        shown = ", ".join(format_cents(v) for v in subtotals)
        r.issues.append(f"The document shows different subtotals ({shown}).")
    if subtotals:
        printed = subtotals[-1]
        r.printed_subtotal_cents = printed
        if _close(goods, printed) or r.tax_included:  # tax-inclusive: subtotal is pre-tax
            pass
        elif r.fees_cents and _close(goods + r.fees_cents, printed):
            fees_in_subtotal = True
        else:
            r.issues.append(
                f"Items add up to {format_cents(goods)} but the receipt's subtotal is "
                f"{format_cents(printed)} (off by {format_cents(abs(goods - printed))})."
            )
        basis = printed
    else:
        basis = goods

    # 2. subtotal + tax + tip + fees ≈ printed total (or amount due on bills)
    if r.tax_included:  # the tax is already inside the item prices
        basis, tax_added = goods, 0
    else:
        tax_added = r.tax_cents
    expected = basis + tax_added + r.tip_cents + (0 if fees_in_subtotal else r.fees_cents)
    if not totals:
        if total_required:
            r.issues.append("No total or amount due was found on the document.")
    else:
        final = totals[-1]
        r.total_cents = final
        r.total_ok = _close(final, expected)
        r.overage_cents = expected - final
        if not r.total_ok:
            parts = [f"subtotal {format_cents(basis)}"]
            if r.tax_cents:
                parts.append(f"tax {format_cents(r.tax_cents)}")
            if r.tip_cents:
                parts.append(f"tip {format_cents(r.tip_cents)}")
            if r.fees_cents and not fees_in_subtotal:
                parts.append(f"fees {format_cents(r.fees_cents)}")
            formula = " + ".join(parts).capitalize()
            r.issues.append(
                f"{formula} = {format_cents(expected)} but the receipt's total is "
                f"{format_cents(final)} (off by {format_cents(abs(final - expected))})."
            )
        # A restaurant slip may print a pre-tip total and a final total; anything else conflicts.
        pre_tip = expected - r.tip_cents
        for other in _distinct(totals):
            if _close(other, final) or (r.tip_cents and _close(other, pre_tip)):
                continue
            shown = f"{format_cents(other)} and {format_cents(final)}"
            r.issues.append(f"The document shows conflicting totals ({shown}).")

    # 3. what was paid − change ≈ total. Receipts print payments in several ways: the amount
    # applied ("CASH 58.40"), the amount handed over ("TENDER 60.00", then "CHANGE 1.60"), or
    # both. Any of them matching confirms the total.
    payments = [line.amount_cents for line in lines if line.kind == "payment"]
    if payments and r.total_cents is not None:
        total = r.total_cents
        net = r.payment_cents - r.change_cents
        confirmed = (
            _close(net, total)
            or any(_close(p, total) for p in payments)
            or any(_close(p - r.change_cents, total) for p in payments)
        )
        if not confirmed:
            paid, change = format_cents(r.payment_cents), format_cents(r.change_cents)
            message = (
                f"Paid {paid} minus change {change} is {format_cents(net)} "
                f"but the total is {format_cents(total)}."
            )
            # If the bill's own arithmetic already confirms the total, a confusing payment
            # section (misread change, a "pay with cash" price offer) is only a note.
            (r.notes if not r.issues else r.issues).append(message)
    return r


def _suspect_rows(r: Reconciliation, textract_values: set[int]) -> list[int]:
    """Summable lines whose amount looks like a summary value: hints for the repair prompt."""
    if r.ok:
        return []
    printed = _printed_values(r.lines) | textract_values
    return [
        line.row
        for line in r.lines
        if line.kind in SUMMABLE and any(_close(abs(line.amount_cents), p) for p in printed)
    ]
