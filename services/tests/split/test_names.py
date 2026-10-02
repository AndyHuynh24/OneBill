import pytest

from split.names import MAX_NAME, parse_names


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Linh, Minh and Bao", ["Linh", "Minh", "Bao"]),
        ("Linh,Minh,Bao", ["Linh", "Minh", "Bao"]),
        ("Linh\nMinh\n\nBao", ["Linh", "Minh", "Bao"]),
        ("Tam & Vy + Khoa; An", ["Tam", "Vy", "Khoa", "An"]),
        ("Linh and Minh, and Bao", ["Linh", "Minh", "Bao"]),  # dictation's Oxford comma
        ("  Linh   Nguyen , bao ", ["Linh Nguyen", "bao"]),  # inner spaces kept, collapsed
        ("Andy and Sandy", ["Andy", "Sandy"]),  # "and" only splits as a whole word
        ("Bao, bao, BAO", ["Bao"]),  # duplicates ignored, first spelling kept
        ("", []),
        (" , and ,", []),
    ],
)
def test_parse_names(text, expected):
    assert parse_names(text) == expected


def test_long_names_are_trimmed():
    assert parse_names("x" * 100) == ["x" * MAX_NAME]


def test_existing_names_are_not_repeated():
    assert parse_names("Bao, Tam", existing=["bao", "Me"]) == ["Tam"]
