"""How sure we are about a reading, from evidence, not from the model's opinion (phase 2d).

Per money line: flagged if the OCR read its amount with low confidence, if the amount isn't
printed on the document, or if the math points at it. When the totals don't add up, a misread is
almost always one wrong, missing, extra, or swapped digit: `one_digit_slips` tries each such
change on each amount with the same math code, and flags the lines where one fixes it, with the
suggested amount.

Overall: "confident" only when nothing is flagged, everything adds up on the first reading, and
the key totals were read crisply; "check" when anything is flagged or the totals don't add up;
"mostly" in between.
"""

from dataclasses import replace
from typing import Any

from documents.grounding import is_printed
from documents.reconcile import Line, check

CHECK_BELOW = 85.0  # OCR confidence (0–100) under which an amount is flagged
CONFIDENT_AT = 95.0  # key totals must be read at least this crisply to say "confident"
KEY_KINDS = frozenset({"subtotal", "tax", "total", "balance_due"})
# Payment, change, and info lines don't affect what anyone owes: never flagged.
CHECKED_KINDS = frozenset(
    {"item", "discount", "fee", "tax", "tip", "subtotal", "total", "balance_due"}
)

HARD_TO_READ = "Hard to read on the photo"
NOT_FOUND = "Not found on the receipt"
OFF_TOTALS = "Doesn't fit the totals"
SLIP = "One digit may be misread"
LEFT_OUT = "Not counted, but it may be an item"
MATH_HINTS = frozenset({SLIP, LEFT_OUT})  # recomputed from the math after every edit
MAX_SLIPS = 3


def assess(
    lines: list[Line],
    ocr_lines: list[str],
    ocr_confidences: list[float] | None,
    *,
    suspect_rows: list[int],
    attempts: int,
    adds_up: bool,
    total_required: bool = True,
) -> dict[str, Any]:
    """`ocr_confidences` align with `ocr_lines` (None for emails, which have no OCR)."""
    text = "\n".join(ocr_lines)
    readings = {line.row: _reading(line.amount_cents, ocr_lines, ocr_confidences) for line in lines}
    # Most actionable first: lines where one digit would make everything add up.
    checks: list[dict[str, Any]] = (
        [] if adds_up else one_digit_slips(lines, readings, total_required, text)
    )
    slipped = {c["row"] for c in checks}
    key_readings: list[float] = []
    for line in lines:
        if line.kind not in CHECKED_KINDS:
            continue
        reading = readings[line.row]
        if line.kind in KEY_KINDS and reading is not None:
            key_readings.append(reading)
        if line.amount_cents != 0 and not is_printed(line.amount_cents, text):
            reason = NOT_FOUND
        elif reading is not None and reading < CHECK_BELOW:
            reason = HARD_TO_READ
        elif line.row in suspect_rows:
            reason = OFF_TOTALS
        else:
            continue
        if line.row in slipped:  # keep the suggestion, with the clearer reason
            next(c for c in checks if c["row"] == line.row)["reason"] = reason
        else:
            checks.append({"row": line.row, "reason": reason})

    if checks or not adds_up:
        level = "check"
    elif attempts == 1 and all(r >= CONFIDENT_AT for r in key_readings):
        level = "confident"
    else:
        level = "mostly"
    return {"level": level, "checks": checks}


def one_digit_slips(
    lines: list[Line],
    readings: dict[int, float | None] | None = None,
    total_required: bool = True,
    printed_text: str = "",
) -> list[dict[str, Any]]:
    """Lines where one changed, added, dropped, or swapped digit fixes a failing check without
    breaking another, suggesting an amount printed on the document if one works, else the
    closest. Least clearly read first, at most 3."""
    original = check(lines, total_required)
    before = len(original.issues)
    if not before:
        return []
    found = []
    # The math only sees each kind's total, so a trial's outcome depends on (kind, change):
    # cache it (an 80-item bill goes from ~8,000 checks to a few hundred).
    outcomes: dict[tuple[str, int], bool] = {}
    for i, line in enumerate(lines):
        if line.kind in CHECKED_KINDS:
            kind, counted, reason = line.kind, line.amount_cents, SLIP
            options = _one_digit_away(line.amount_cents)
        elif line.kind == "info" and line.amount_cents > 0:
            # Left out of the math (e.g. a bag whose "$2.97" OCR read as "52.97"): would
            # counting it as an item, as printed or one digit off, make it add up?
            kind, counted, reason = "item", 0, LEFT_OUT
            options = {line.amount_cents} | {c for c in _one_digit_away(line.amount_cents) if c > 0}
        else:
            continue
        fixes = []
        for cents in sorted(options, key=lambda c: abs(c - line.amount_cents)):
            key = (kind, cents - counted)  # same change to a kind's total → same outcome
            if key not in outcomes:
                trial = [*lines[:i], replace(line, kind=kind, amount_cents=cents), *lines[i + 1 :]]
                result = check(trial, total_required)
                # Fewer problems, same tax treatment: a change that only "works" by making the
                # items look tax-inclusive is a coincidence, not a fix.
                outcomes[key] = (
                    len(result.issues) < before and result.tax_included == original.tax_included
                )
            if outcomes[key]:
                fixes.append(cents)
        if fixes:
            # An amount actually printed on the document first, else the closest.
            printed = [c for c in fixes if printed_text and is_printed(c, printed_text)]
            check_ = {"row": line.row, "reason": reason, "suggestedCents": (printed or fixes)[0]}
            found.append(check_ | ({"suggestedKind": kind} if kind != line.kind else {}))
    readings = readings or {}
    found.sort(key=lambda c: 100.0 if readings.get(c["row"]) is None else readings[c["row"]])
    return found[:MAX_SLIPS]


def after_edit(
    before: dict[str, Any],
    old_lines: list[Line],
    new_lines: list[Line],
    total_required: bool = True,
) -> dict[str, Any]:
    """Confidence after the user edits lines on the bill: flags on untouched lines stay, edited
    lines are trusted, and if it still doesn't add up, fresh one-digit suggestions are offered.
    No OCR or model is involved."""
    old = {line.row: line for line in old_lines}
    new = {line.row: line for line in new_lines}

    def untouched(row: int) -> bool:
        a, b = old.get(row), new.get(row)
        return (
            a is not None
            and b is not None
            and (a.description, a.kind, a.amount_cents)
            == (
                b.description,
                b.kind,
                b.amount_cents,
            )
        )

    kept = [
        {k: v for k, v in c.items() if k != "suggestedCents"} | {"row": int(c["row"])}
        for c in before.get("checks", [])
        if c.get("reason") not in MATH_HINTS and untouched(int(c["row"]))
    ]
    adds_up = check(new_lines, total_required).ok
    if not adds_up:
        rows = {c["row"] for c in kept}
        kept = [
            s for s in one_digit_slips(new_lines, None, total_required) if s["row"] not in rows
        ] + kept
    return {"level": "confirmed" if adds_up and not kept else "check", "checks": kept}


def _one_digit_away(cents: int) -> set[int]:
    """Amounts one changed, added, dropped, or swapped digit away (the usual misreads)."""
    sign, digits = (-1 if cents < 0 else 1), str(abs(cents))
    found = set()
    for i in range(len(digits) + 1):
        for d in "0123456789":
            found.add(digits[:i] + d + digits[i:])  # an extra digit
            if i < len(digits):
                found.add(digits[:i] + d + digits[i + 1 :])  # a changed digit
        if i < len(digits) and len(digits) > 1:
            found.add(digits[:i] + digits[i + 1 :])  # a dropped digit
        if i + 1 < len(digits):
            found.add(digits[:i] + digits[i + 1] + digits[i] + digits[i + 2 :])  # swapped pair
    return {sign * int(v) for v in found} - {cents}


def _reading(cents: int, ocr_lines: list[str], confidences: list[float] | None) -> float | None:
    """The lowest OCR confidence among the lines printing this amount (the safe side)."""
    if not confidences or len(confidences) != len(ocr_lines) or cents == 0:
        return None
    found = [c for line, c in zip(ocr_lines, confidences, strict=True) if is_printed(cents, line)]
    return min(found) if found else None
