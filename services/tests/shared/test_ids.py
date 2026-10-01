import pytest

from shared.ids import is_valid_id, new_id


def test_ids_sort_by_creation_time():
    earlier = new_id(now_ms=1_790_000_000_000)
    later = new_id(now_ms=1_790_000_000_001)

    assert earlier < later


def test_id_format_is_fixed_width_lowercase():
    value = new_id(now_ms=1_790_000_000_000)

    assert len(value) == 21
    assert value[:13] == "1790000000000"
    assert is_valid_id(value)


def test_ids_created_in_the_same_millisecond_differ():
    assert new_id(now_ms=1) != new_id(now_ms=1)


@pytest.mark.parametrize(
    "value",
    ["", "abc", "1790000000000ABCDEF12", "../../etc/passwd", "1790000000000abcdef1", None],
)
def test_rejects_malformed_ids(value):
    """IDs arrive in URL paths, so anything unexpected is refused before it reaches a key."""
    assert not is_valid_id(value)
