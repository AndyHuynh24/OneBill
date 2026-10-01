"""Recent friends: the one-tap chips on "Who's here?". Names only; friends don't need accounts."""

from datetime import UTC, datetime, timedelta

import pytest

from split.friends import (
    InvalidFriend,
    delete_friend,
    friend_id,
    list_friends,
    remember_friends,
)

NOW = datetime(2026, 10, 1, 19, 0, tzinfo=UTC)


def test_remembered_friends_are_listed_most_used_first(table):
    remember_friends(table, "user-a", ["Bao", "Linh"], NOW)
    remember_friends(table, "user-a", ["bao", "Minh"], NOW + timedelta(days=1))
    remember_friends(table, "user-a", ["Bao"], NOW + timedelta(days=2))

    friends = list_friends(table, "user-a")

    assert [f["name"] for f in friends] == ["Bao", "Minh", "Linh"]  # 3 uses, then most recent
    assert friends[0]["timesUsed"] == 3
    assert friends[0]["lastUsedAt"] == (NOW + timedelta(days=2)).isoformat()


def test_same_name_in_any_case_is_one_friend_with_the_latest_spelling(table):
    remember_friends(table, "user-a", ["bao"], NOW)
    remember_friends(table, "user-a", ["Bao"], NOW)

    assert [f["name"] for f in list_friends(table, "user-a")] == ["Bao"]


def test_a_friend_keeps_their_color(table):
    remember_friends(table, "user-a", ["Bao"], NOW)
    color = list_friends(table, "user-a")[0]["color"]
    remember_friends(table, "user-a", ["Bao"], NOW)

    assert list_friends(table, "user-a")[0]["color"] == color
    assert 0 <= color < 12


def test_friends_are_private_to_each_user(table):
    remember_friends(table, "user-a", ["Bao"], NOW)

    assert list_friends(table, "user-b") == []


def test_delete_removes_a_chip_and_is_idempotent(table):
    remember_friends(table, "user-a", ["Bao", "Linh"], NOW)

    delete_friend(table, "user-a", friend_id("Bao"))
    delete_friend(table, "user-a", friend_id("Bao"))  # already gone: still fine

    assert [f["name"] for f in list_friends(table, "user-a")] == ["Linh"]


def test_ids_reveal_nothing_about_the_name_and_are_validated(table):
    fid = friend_id("Bao")

    assert len(fid) == 16
    assert "bao" not in fid.lower()
    assert friend_id("BAO") == fid
    with pytest.raises(InvalidFriend):
        delete_friend(table, "user-a", "../../PROFILE")


def test_list_is_capped(table):
    remember_friends(table, "user-a", [f"Friend {i}" for i in range(60)], NOW)

    assert len(list_friends(table, "user-a")) == 50
