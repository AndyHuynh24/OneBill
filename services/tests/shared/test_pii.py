import pytest

from shared.pii import redact


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("VISA ****1234", "VISA [CARD]"),
        ("CARD XXXX XXXX XXXX 9876", "CARD [CARD]"),
        ("Acct: ************4321", "Acct: [CARD]"),
        ("4111 1111 1111 1111", "[CARD]"),
        ("4111-1111-1111-1111", "[CARD]"),
        ("4111111111111111", "[CARD]"),
        ("ACCOUNT 123456789012", "ACCOUNT [ACCOUNT]"),
        ("REF 98765432", "REF [ACCOUNT]"),
        ("Email receipt to jane.doe+shop@example.com", "Email receipt to [EMAIL]"),
    ],
)
def test_masks_card_account_and_email(text, expected):
    assert redact(text).text == expected


@pytest.mark.parametrize(
    "text",
    [
        "TOTAL 23.18",
        "MILK 1 GAL 4.19",
        "1,234.56",
        "09/30/2026 14:05",
        "2026-09-30",
        "DATE 20260930",  # 8 digits that form a valid date are kept
        "STORE #1234 TEL (555) 123-4567",
        "123 MAIN ST SPRINGFIELD IL 62704",
        "QTY 2 @ 1.29",
    ],
)
def test_keeps_prices_dates_and_store_details(text):
    assert redact(text).text == text


def test_reports_what_was_masked_without_the_values():
    result = redact("VISA ****1234\nbob@example.com\nREF 98765432")

    assert result.counts == {"card": 1, "email": 1, "account": 1}
    assert "1234" not in result.text
    assert "bob" not in result.text


def test_multiline_receipt_keeps_structure():
    text = "TARGET\nBANANAS 1.29\nVISA ****1234\nTOTAL 1.29"

    assert redact(text).text == "TARGET\nBANANAS 1.29\nVISA [CARD]\nTOTAL 1.29"


# --- address blocks: the customer's name and address never reach the model --------

from types import SimpleNamespace  # noqa: E402

from shared.pii import address_lines  # noqa: E402


def test_a_card_tail_written_as_ending_in_is_masked():
    assert redact("VISA Ending in 6262").text == "VISA Ending in [CARD]"
    assert redact("Mastercard ending 0042 $26.18").text == "Mastercard ending [CARD] $26.18"


def test_a_postal_code_line_and_the_street_above_it_are_address():
    lines = ["TARGET", "1233 Colonel By Dr", "Ottawa, ON K1S 5B7", "BANANAS 1.29"]

    assert address_lines(lines) == {1, 2}


def test_a_us_zip_line_is_address_but_the_store_name_above_it_isnt():
    lines = ["TARGET", "MINNEAPOLIS, MN 55401", "BANANAS 1.29"]

    assert address_lines(lines) == {1}  # "TARGET" has no house number


def test_lines_under_a_ship_to_heading_are_address_until_a_money_line():
    lines = ["Ship to", "Jane Doe", "12 Elm St", "Widget 4.99", "Total 4.99"]

    assert address_lines(lines) == {1, 2}


def test_with_positions_the_lines_below_the_heading_are_used_not_the_next_in_reading_order():
    """Walmart: three columns, so reading order puts a product between the heading and the name."""

    def box(left, top, text):
        return SimpleNamespace(left=left, top=top, right=left + 0.2, bottom=top + 0.01, height=0.01)

    lines = ["Delivery Address", "Mainstays Bath Towel", "Hoang Phu Xin", "Colonel By Dr, Ottawa"]
    boxes = [box(0.7, 0.75, ""), box(0.4, 0.755, ""), box(0.7, 0.763, ""), box(0.7, 0.775, "")]

    assert address_lines(lines, boxes) == {2, 3}


def test_lines_matching_textracts_customer_fields_are_address():
    lines = ["Thanks for shopping", "Hoang Phu Xin", "AXE Deodorant 3.96"]

    assert address_lines(lines, customer_values=["Hoang Phu Xin"]) == {1}


def test_sensitive_spans_are_exactly_what_redact_masks():
    from shared.pii import sensitive_spans

    text = "VISA Ending in 6262 Account 004412345678 a@b.co 20260930"

    spans = sensitive_spans(text)

    assert [text[s:e] for s, e in spans] == ["6262", "004412345678", "a@b.co"]


def test_a_product_code_on_an_item_line_isnt_blacked_out():
    from shared.pii import sensitive_spans

    assert sensitive_spans("GV MILK 007874235186 3.48") == []


def test_a_stores_own_address_fields_dont_mask_the_store_name():
    """On a paper receipt, Textract's generic ADDRESS field holds the store's address."""
    lines = ["Beef-a-Roo", "6116 N. 2nd St.", "Loves Park, IL 61111", "Cheeseburger 4.99"]

    assert 0 not in address_lines(lines)


def test_a_product_code_next_to_a_price_keeps_the_price():
    assert redact("GV MILK 007874235186 3.48").text == "GV MILK [ACCOUNT] 3.48"
