import json

import pytest

from run_eval import init_labels, labelled_expectations
from score import aggregate, render_markdown, score_receipt, vendors_match

EXPECTED = {
    "vendor": "Target",
    "document_date": "2026-09-28",
    "subtotal_cents": 2448,
    "tax_cents": 170,
    "tip_cents": 0,
    "total_cents": 2618,
    "item_count": 5,
    "deadlines": [{"type": "return_by", "due_date": "2026-10-28"}],
}

PREDICTED = {
    "vendor": "TARGET #1234",
    "document_date": "2026-09-28",
    "subtotal_cents": 2448,
    "tax_cents": 170,
    "tip_cents": 0,
    "total_cents": 2618,
    "item_count": 5,
    "lines": [],
    "deadlines": [{"type": "return_by", "due_date": "2026-10-28"}],
    "status": "ready",
    "attempts": 1,
}


@pytest.mark.parametrize(
    ("expected", "predicted", "match"),
    [
        ("Target", "TARGET", True),
        ("Target", "Target #1234 Springfield", True),
        ("Trader Joe's", "TRADER JOES", True),
        ("Target", "Walmart", False),
        ("Target", None, False),
    ],
)
def test_vendor_matching_is_forgiving_about_case_punctuation_and_store_numbers(
    expected, predicted, match
):
    assert vendors_match(expected, predicted) is match


def test_perfect_receipt_scores_every_labelled_field_correct():
    row = score_receipt(EXPECTED, PREDICTED)

    assert row["fields"] == {
        "vendor": True,
        "document_date": True,
        "subtotal_cents": True,
        "tax_cents": True,
        "tip_cents": True,
        "total_cents": True,
        "item_count": True,
    }
    assert row["deadlines"] == {"tp": 1, "fp": 0, "fn": 0}


def test_wrong_total_is_counted_wrong():
    row = score_receipt(EXPECTED, PREDICTED | {"total_cents": 8236})  # TOTAL summed as an item

    assert row["fields"]["total_cents"] is False


def test_unlabelled_fields_are_not_scored():
    row = score_receipt({"total_cents": 2618}, PREDICTED)

    assert row["fields"] == {"total_cents": True}


def test_deadline_precision_and_recall_counts():
    predicted = PREDICTED | {
        "deadlines": [
            {"type": "return_by", "due_date": "2026-10-28"},
            {"type": "warranty_expires", "due_date": "2027-09-28"},  # not on the receipt
        ]
    }
    expected = EXPECTED | {
        "deadlines": [
            {"type": "return_by", "due_date": "2026-10-28"},
            {"type": "payment_due", "due_date": "2026-10-15"},  # missed
        ]
    }

    assert score_receipt(expected, predicted)["deadlines"] == {"tp": 1, "fp": 1, "fn": 1}


def test_line_kind_accuracy_when_labelled():
    expected = {
        "lines": [
            {"description": "TOTAL", "kind": "total"},
            {"description": "Bananas", "kind": "item"},
        ]
    }
    predicted = PREDICTED | {
        "lines": [
            {"description": "BANANAS", "kind": "item"},
            {"description": "TOTAL", "kind": "item"},  # wrong
        ]
    }

    assert score_receipt(expected, predicted)["line_kinds"] == {"correct": 1, "total": 2}


def test_aggregate_splits_plain_and_tricky():
    rows = [
        score_receipt(EXPECTED, PREDICTED) | {"tricky": False, "status": "ready", "attempts": 1},
        score_receipt(EXPECTED, PREDICTED | {"total_cents": 1})
        | {"tricky": True, "status": "needs_review", "attempts": 2},
    ]

    summary = aggregate(rows)

    assert summary["all"]["fields"]["total_cents"] == {"correct": 1, "total": 2}
    assert summary["plain"]["fields"]["total_cents"] == {"correct": 1, "total": 1}
    assert summary["tricky"]["fields"]["total_cents"] == {"correct": 0, "total": 1}
    assert summary["all"]["needs_review"] == 1
    assert summary["all"]["repaired"] == 1
    assert summary["all"]["count"] == 2


def test_markdown_report_has_a_table_and_no_receipt_details():
    rows = [
        score_receipt(EXPECTED, PREDICTED)
        | {"tricky": False, "status": "ready", "attempts": 1, "file": "x.jpg"}
    ]

    report = render_markdown(
        aggregate(rows), {"prompt_version": "v1", "model": "haiku", "date": "2026-10-01"}
    )

    assert "| Field |" in report
    assert "Total" in report
    assert "100%" in report
    assert "x.jpg" not in report  # aggregate only: safe to commit
    assert "Target" not in report


# --- runner helpers (no AWS) --------------------------------------------------------------------


def test_init_labels_adds_stubs_for_new_files_only(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "b.pdf").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("ignore me")
    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps(
            {"receipts": [{"file": "a.jpg", "tricky": True, "expected": {"total_cents": 1}}]}
        )
    )

    added = init_labels(tmp_path, labels)

    assert added == ["b.pdf"]
    data = json.loads(labels.read_text())
    assert [r["file"] for r in data["receipts"]] == ["a.jpg", "b.pdf"]
    assert data["receipts"][0]["expected"] == {"total_cents": 1}  # existing label untouched


def test_null_label_fields_are_not_scored():
    assert labelled_expectations({"vendor": None, "total_cents": 2618, "deadlines": []}) == {
        "total_cents": 2618,
        "deadlines": [],
    }


def test_fees_such_as_a_service_charge_are_scored():
    expected = {"fees_cents": 540, "item_count": 2}
    right = PREDICTED | {"fees_cents": 540, "item_count": 2}
    wrong = PREDICTED | {"fees_cents": 0, "item_count": 3}  # service charge counted as an item

    assert score_receipt(expected, right)["fields"] == {"fees_cents": True, "item_count": True}
    assert score_receipt(expected, wrong)["fields"] == {"fees_cents": False, "item_count": False}
