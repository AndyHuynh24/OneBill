from datetime import date

from documents.extraction import ExtractionOutcome, parse_output
from documents.reconcile import reconcile
from documents.textract import ExpenseResult, SummaryField
from documents.validate import validate

OCR_LINES = [
    "TARGET",
    "09/28/2026 14:05",
    "BANANAS 1.29",
    "TOTAL 1.29",
    "PAYMENT DUE WITHIN 30 DAYS",
    "OF INVOICE DATE",
]


def expense(lines=OCR_LINES, total_confidence=98.0):
    return ExpenseResult(
        pages=1,
        lines=list(lines),
        summary_fields=[SummaryField("TOTAL", "TOTAL", "1.29", total_confidence, 129)],
    )


def outcome(
    deadlines=(), document_date="2026-09-28", lines=None, currency="USD", doc_type="receipt"
):
    tool_input = {
        "doc_type": doc_type,
        "vendor": "TARGET",
        "document_date": document_date,
        "currency": currency,
        "lines": lines
        or [
            {"description": "BANANAS", "amount": "1.29", "kind": "item"},
            {"description": "TOTAL", "amount": "1.29", "kind": "total"},
        ],
        "deadlines": list(deadlines),
    }
    document = parse_output(tool_input)
    return ExtractionOutcome(
        document=document,
        reconciliation=reconcile(document.lines),
        attempts=1,
        usage={"inputTokens": 1, "outputTokens": 1},
        model_id="m",
    )


DUE_30 = {
    "type": "payment_due",
    "date": None,
    "days_after_purchase": 30,
    "evidence": "PAYMENT DUE WITHIN 30 DAYS",
}


def test_clean_receipt_is_ready():
    result = validate(outcome(), expense())

    assert result.status == "ready"
    assert result.issues == []
    assert result.total_cents == 129
    assert result.vendor == "TARGET"


def test_return_window_becomes_a_date_computed_by_code():
    result = validate(outcome([DUE_30], doc_type="bill"), expense())

    assert [(o.type, o.due_date) for o in result.obligations] == [
        ("payment_due", date(2026, 10, 28))
    ]
    assert result.obligations[0].amount_cents == 129


def test_code_arithmetic_wins_over_a_model_supplied_date():
    wrong = DUE_30 | {"date": "2026-10-30"}  # model miscounted the window

    result = validate(outcome([wrong], doc_type="bill"), expense())

    assert result.obligations[0].due_date == date(2026, 10, 28)


def test_printed_due_date_is_used_as_is():
    due = {
        "type": "payment_due",
        "date": "2026-10-15",
        "days_after_purchase": None,
        "evidence": "DUE 10/15/2026",
    }

    result = validate(outcome([due], doc_type="bill"), expense(OCR_LINES + ["DUE 10/15/2026"]))

    assert result.obligations[0].due_date == date(2026, 10, 15)


def test_evidence_matching_ignores_case_spacing_and_line_breaks():
    across_lines = DUE_30 | {"evidence": "payment due within 30 days of invoice date"}

    result = validate(outcome([across_lines], doc_type="bill"), expense())

    assert result.status == "ready"
    assert len(result.obligations) == 1


def test_invented_deadline_is_dropped_and_flagged():
    invented = {
        "type": "warranty_expires",
        "date": None,
        "days_after_purchase": 365,
        "evidence": "ONE YEAR WARRANTY",
    }

    result = validate(outcome([invented], doc_type="bill"), expense())

    assert result.obligations == []
    assert result.status == "needs_review"
    assert any("isn't printed" in issue for issue in result.issues)


def test_window_without_purchase_date_cannot_be_dated():
    result = validate(outcome([DUE_30], document_date=None, doc_type="bill"), expense())

    assert result.obligations == []
    assert result.status == "needs_review"
    assert any("purchase date" in issue for issue in result.issues)


def test_implausible_due_date_is_rejected():
    far = {
        "type": "payment_due",
        "date": "2041-01-01",
        "days_after_purchase": None,
        "evidence": "TOTAL 1.29",
    }

    result = validate(outcome([far], doc_type="bill"), expense())

    assert result.obligations == []
    assert result.status == "needs_review"


def test_hard_to_read_total_needs_review():
    result = validate(outcome(), expense(total_confidence=62.0))

    assert result.status == "needs_review"
    assert any("hard to read" in issue for issue in result.issues)


def test_reconciliation_issues_carry_through():
    lines = [
        {"description": "BANANAS", "amount": "1.29", "kind": "item"},
        {"description": "TOTAL", "amount": "5.00", "kind": "total"},
    ]

    result = validate(outcome(lines=lines), expense())

    assert result.status == "needs_review"
    assert result.total_cents == 500  # printed total kept for display
    assert any("off by" in issue for issue in result.issues)


def test_missing_currency_falls_back_to_the_profile_currency():
    result = validate(outcome(currency=None), expense(), default_currency="USD")

    assert result.currency == "USD"


def test_lines_are_kept_with_their_kinds_for_the_review_screen():
    result = validate(outcome(), expense())

    assert [(line["description"], line["kind"], line["amountCents"]) for line in result.lines] == [
        ("BANANAS", "item", 129),
        ("TOTAL", "total", 129),
    ]


def test_payment_notes_are_kept_without_forcing_review():
    lines = [
        {"description": "BANANAS", "amount": "1.29", "kind": "item"},
        {"description": "TOTAL", "amount": "1.29", "kind": "total"},
        {"description": "PAY WITH CASH", "amount": "1.25", "kind": "payment"},
    ]

    result = validate(outcome(lines=lines), expense(OCR_LINES + ["PAY WITH CASH 1.25"]))

    assert result.status == "ready"
    assert len(result.notes) == 1
    assert "Paid $1.25" in result.notes[0]


def test_amount_not_printed_on_the_document_needs_review():
    lines = [
        {"description": "BANANAS", "amount": "1.29", "kind": "item"},
        {"description": "TOTAL", "amount": "1.29", "kind": "total"},
        {"description": "SERVICE CHRG", "amount": "1.50", "kind": "info"},  # not printed
    ]

    result = validate(outcome(lines=lines), expense())

    assert result.status == "needs_review"
    assert any("$1.50" in issue and "isn't printed" in issue for issue in result.issues)


WARRANTY_1Y = {
    "type": "warranty_expires",
    "date": None,
    "days_after_purchase": 365,
    "evidence": "PAYMENT DUE WITHIN 30 DAYS",  # printed, so only the scope rule applies
}


def test_receipts_make_no_reminders_not_even_for_a_printed_return_policy():
    """Receipts are for splitting; a deadline the model finds on one is dropped quietly."""
    returns = DUE_30 | {"type": "warranty_expires"}

    result = validate(outcome([returns, WARRANTY_1Y]), expense())

    assert result.obligations == []
    assert result.status == "ready"
    assert result.issues == []


def test_bills_keep_their_deadline_kinds():
    due = {**WARRANTY_1Y, "type": "payment_due", "days_after_purchase": 14}

    result = validate(outcome([due], doc_type="bill"), expense())

    assert [o.type for o in result.obligations] == ["payment_due"]


def test_a_clean_reading_is_confident_and_a_reviewed_one_asks_for_a_check():
    clean = validate(outcome(), expense())
    vague = validate(outcome(), expense(total_confidence=50.0))

    assert clean.confidence == {"level": "confident", "checks": []}
    assert vague.confidence["level"] == "check"
