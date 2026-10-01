"""Final checks before saving: turn deadlines into dates, reject invented ones, decide the status.

Everything here is deterministic code. The model's output is treated as a proposal:
- deadline dates are computed from the purchase date (never trusted from the model),
- every deadline must quote text that is actually printed on the document,
- low OCR confidence or arithmetic that doesn't reconcile sends the document to review.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from documents.extraction import Deadline, ExtractionOutcome
from documents.textract import ExpenseResult
from shared.dates import parse_iso_date
from shared.pii import redact

MIN_TOTAL_CONFIDENCE = 80.0
MAX_YEARS_AHEAD = 10


@dataclass(frozen=True)
class Obligation:
    type: str
    due_date: date
    evidence: str
    amount_cents: int | None


@dataclass
class ValidationResult:
    status: str  # "ready" | "needs_review"
    issues: list[str]
    doc_type: str
    vendor: str | None
    document_date: str | None
    currency: str | None
    total_cents: int | None
    subtotal_cents: int | None
    tax_cents: int
    tip_cents: int
    fees_cents: int
    discounts_cents: int
    lines: list[dict[str, Any]]
    corrections: list[dict[str, Any]]
    obligations: list[Obligation] = field(default_factory=list)


def validate(
    outcome: ExtractionOutcome, expense: ExpenseResult, default_currency: str = "USD"
) -> ValidationResult:
    doc = outcome.document
    rec = outcome.reconciliation
    issues = list(rec.issues) + list(doc.warnings)

    confidence = expense.total_confidence
    if confidence is not None and confidence < MIN_TOTAL_CONFIDENCE:
        issues.append(
            f"The total was hard to read (OCR confidence {confidence:.0f}%). Please check it."
        )

    printed_text = _searchable(expense.lines)
    purchase_date = parse_iso_date(doc.document_date)
    obligations: list[Obligation] = []
    for deadline in doc.deadlines:
        if not _is_printed(deadline.evidence, printed_text):
            issues.append(
                f'A deadline ("{deadline.evidence}") isn\'t printed on the document, '
                "so it was ignored."
            )
            continue
        due = _due_date(deadline, purchase_date)
        if due is None:
            issues.append(
                f"The purchase date wasn't found, so the {_label(deadline.type)} deadline "
                "couldn't be calculated."
            )
            continue
        if purchase_date and not purchase_date - timedelta(days=1) <= due <= _years_after(
            purchase_date
        ):
            issues.append(
                f"The {_label(deadline.type)} date {due.isoformat()} looks wrong, "
                "so it was ignored."
            )
            continue
        obligations.append(Obligation(deadline.type, due, deadline.evidence, rec.total_cents))

    return ValidationResult(
        status="needs_review" if issues else "ready",
        issues=issues,
        doc_type=doc.doc_type,
        vendor=doc.vendor,
        document_date=doc.document_date,
        currency=doc.currency or default_currency,
        total_cents=rec.total_cents,
        subtotal_cents=rec.printed_subtotal_cents,
        tax_cents=rec.tax_cents,
        tip_cents=rec.tip_cents,
        fees_cents=rec.fees_cents,
        discounts_cents=rec.discounts_cents,
        lines=[
            {
                "row": line.row,
                "description": line.description,
                "kind": line.kind,
                "amountCents": line.amount_cents,
            }
            for line in rec.lines
        ],
        corrections=[
            {
                "row": c.row,
                "description": c.description,
                "from": c.from_kind,
                "to": c.to_kind,
                "reason": c.reason,
            }
            for c in rec.corrections
        ],
        obligations=obligations,
    )


def _due_date(deadline: Deadline, purchase_date: date | None) -> date | None:
    # A window ("within 30 days") is computed here from the purchase date; code does the arithmetic.
    if deadline.days_after_purchase is not None:
        if purchase_date is None:
            return None
        return purchase_date + timedelta(days=deadline.days_after_purchase)
    return parse_iso_date(deadline.date)


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", text.upper()).split())


def _searchable(lines: list[str]) -> list[str]:
    """The printed text, raw and PII-masked (the model only saw the masked version)."""
    raw = "\n".join(lines)
    return [_normalize(raw), _normalize(redact(raw).text)]


def _is_printed(evidence: str, haystacks: list[str]) -> bool:
    needle = _normalize(evidence)
    return bool(needle) and any(needle in hay for hay in haystacks)


def _years_after(start: date) -> date:
    try:
        return start.replace(year=start.year + MAX_YEARS_AHEAD)
    except ValueError:  # Feb 29
        return start.replace(year=start.year + MAX_YEARS_AHEAD, day=28)


def _label(deadline_type: str) -> str:
    return deadline_type.replace("_", " ")
