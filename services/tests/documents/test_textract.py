import json
from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber

from documents.textract import analyze_sync, fetch_async, parse_expense, start_async

FIXTURES = Path(__file__).parent.parent / "fixtures" / "textract"


def load(name):
    return json.loads((FIXTURES / name).read_text())


# --- parsing ------------------------------------------------------------------------------------


def test_parses_summary_money_fields_to_cents():
    result = parse_expense([load("grocery_sync.json")])

    assert result.summary_money == {
        "SUBTOTAL": 2448,
        "TAX": 170,
        "TOTAL": 2618,
        "AMOUNT_PAID": 3000,
    }


def test_extracts_vendor_and_date_hints():
    result = parse_expense([load("grocery_sync.json")])

    assert result.vendor == "TARGET"
    assert result.date_text == "09/28/2026"
    assert result.currency == "USD"


def test_keeps_textract_line_item_rows_with_numbers_and_parsed_prices():
    result = parse_expense([load("grocery_sync.json")])

    assert [r.description for r in result.line_items][:3] == ["BANANAS", "MILK 1 GAL", "BREAD"]
    assert result.line_items[0].row == 1
    assert result.line_items[0].price_cents == 129
    assert result.line_items[5].price_cents == -200  # "2.00-" is a discount
    # Textract's own grouping includes summary rows; they're kept here and sorted out later
    assert "TOTAL" in [r.description for r in result.line_items]


def test_raw_lines_in_reading_order():
    result = parse_expense([load("grocery_sync.json")])

    assert result.lines[0] == "TARGET"
    assert "TOTAL 26.18" in result.lines
    assert result.lines[-1].startswith("RETURNS ACCEPTED")


def test_reports_confidence_of_the_total():
    result = parse_expense([load("grocery_sync.json")])

    assert result.total_confidence == pytest.approx(96.4)


def test_merges_paginated_async_results_across_pages():
    result = parse_expense(load("restaurant_async_pages.json"))

    assert result.pages == 2
    assert result.vendor == "BLUE DOOR BISTRO"
    assert result.summary_money["TOTAL"] == 2689
    assert result.summary_money["GRATUITY"] == 400
    assert [r.description for r in result.line_items] == ["BURGER", "FRIES"]
    assert result.lines[:2] == ["BLUE DOOR BISTRO", "2026-09-27"]
    assert result.lines[-1] == "TOTAL 26.89"


def test_low_confidence_fields_are_listed():
    result = parse_expense(load("restaurant_async_pages.json"))

    assert "GRATUITY" in result.low_confidence_fields


def test_render_for_model_has_three_labelled_sections():
    text = parse_expense([load("grocery_sync.json")]).render_for_model()

    assert "<textract_summary_fields>" in text
    assert "TOTAL: $26.18" in text
    assert "<textract_line_items>" in text
    assert "row 1: BANANAS | $1.29" in text
    assert "<ocr_lines>" in text
    assert "TOTAL 26.18" in text


def test_empty_response_is_safe():
    result = parse_expense([{"DocumentMetadata": {"Pages": 1}, "ExpenseDocuments": []}])

    assert result.pages == 1
    assert result.lines == []
    assert result.summary_money == {}


# --- API calls (stubbed: never real Textract in unit tests) ------------------------------------


@pytest.fixture
def textract():
    client = boto3.client("textract", region_name="us-east-1")
    with Stubber(client) as stub:
        yield client, stub


def test_analyze_sync_reads_the_s3_object(textract):
    client, stub = textract
    stub.add_response(
        "analyze_expense",
        load("grocery_sync.json"),
        {"Document": {"S3Object": {"Bucket": "docs", "Name": "uploads/u/1"}}},
    )

    assert analyze_sync(client, "docs", "uploads/u/1")[0]["DocumentMetadata"]["Pages"] == 1


TOPIC = "arn:aws:sns:us-east-1:123456789012:textract-done"
ROLE = "arn:aws:iam::123456789012:role/textract-publish"


def test_start_async_tags_the_job_with_the_doc_id_and_notifies_sns(textract):
    client, stub = textract
    stub.add_response(
        "start_expense_analysis",
        {"JobId": "job-123"},
        {
            "DocumentLocation": {"S3Object": {"Bucket": "docs", "Name": "uploads/u/1"}},
            "JobTag": "1790000000000aaaaaaaa",
            "NotificationChannel": {"SNSTopicArn": TOPIC, "RoleArn": ROLE},
            "ClientRequestToken": "1790000000000aaaaaaaa",
        },
    )

    job = start_async(client, "docs", "uploads/u/1", "1790000000000aaaaaaaa", TOPIC, ROLE)

    assert job == "job-123"


def test_fetch_async_follows_next_token(textract):
    client, stub = textract
    page1, page2 = load("restaurant_async_pages.json")
    stub.add_response("get_expense_analysis", page1, {"JobId": "job-123"})
    stub.add_response("get_expense_analysis", page2, {"JobId": "job-123", "NextToken": "token-2"})

    pages = fetch_async(client, "job-123")

    assert len(pages) == 2


def test_fetch_async_raises_when_the_job_failed(textract):
    client, stub = textract
    stub.add_response(
        "get_expense_analysis",
        {"JobStatus": "FAILED", "StatusMessage": "Unsupported document"},
        {"JobId": "job-123"},
    )

    with pytest.raises(RuntimeError, match="Unsupported document"):
        fetch_async(client, "job-123")
