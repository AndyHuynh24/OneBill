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
