"""Split math: exact fractions first, one largest-remainder rounding, shares sum to the total."""

from fractions import Fraction

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from split.allocate import (
    EVEN,
    PROPORTIONAL,
    SplitError,
    SplitLine,
    allocate,
    exact_shares,
    one_person,
)

ME, BAO, LINH, MINH = "me", "bao", "linh", "minh"


def item(row, cents, kind="item"):
    return SplitLine(row=row, kind=kind, amount_cents=cents)


# --- worked examples -------------------------------------------------------------------------


def test_dinner_for_three_with_proportional_tax_and_tip():
    lines = [
        item(1, 1450),  # pad thai: bao
        item(2, 1200),  # curry: linh
        item(3, 900),  # spring rolls: shared by everyone
        item(4, 296, "tax"),
        item(5, 710, "tip"),
    ]
    assignments = {1: [BAO], 2: [LINH], 3: [ME, BAO, LINH]}

    result = allocate(
        members=[ME, BAO, LINH],
        lines=lines,
        assignments=assignments,
        charges={4: PROPORTIONAL, 5: PROPORTIONAL},
        total_cents=4556,
    )

    assert sum(s.total_cents for s in result) == 4556
    by = {s.member: s for s in result}
    # bao ordered 14.50 + 3.00 = 17.50 of 35.50 in items → 49.3% of tax and tip
    assert by[BAO].total_cents == 2246
    assert by[BAO].rows == [1, 3]
    assert by[BAO].items_cents == 1750
    assert by[BAO].extras_cents == 2246 - 1750


def test_tip_paid_by_one_person_and_tax_split_evenly():
    lines = [item(1, 1000), item(2, 2000), item(3, 300, "tax"), item(4, 600, "tip")]

    result = allocate(
        members=[ME, BAO],
        lines=lines,
        assignments={1: [ME], 2: [BAO]},
        charges={3: EVEN, 4: one_person(ME)},
        total_cents=3900,
    )

    by = {s.member: s.total_cents for s in result}
    assert by == {ME: 1000 + 150 + 600, BAO: 2000 + 150}


def test_discount_reduces_whoever_it_is_assigned_to():
    lines = [item(1, 500), item(2, -100, "discount"), item(3, 700)]

    result = allocate(
        members=[ME, BAO],
        lines=lines,
        assignments={1: [ME], 2: [ME], 3: [BAO]},
        charges={},
        total_cents=1100,
    )

    assert {s.member: s.total_cents for s in result} == {ME: 400, BAO: 700}


def test_leftover_cent_goes_to_the_largest_remainder_then_seat_order():
    # $1.00 shared three ways: 33.33… each; one extra cent goes to the first seat.
    result = allocate(
        members=[ME, BAO, LINH],
        lines=[item(1, 100)],
        assignments={1: [ME, BAO, LINH]},
        charges={},
        total_cents=100,
    )

    assert [s.total_cents for s in result] == [34, 33, 33]


def test_proportional_charge_falls_back_to_even_when_nobody_has_items():
    result = allocate(
        members=[ME, BAO],
        lines=[item(1, 500, "fee")],
        assignments={},
        charges={1: PROPORTIONAL},
        total_cents=500,
    )

    assert [s.total_cents for s in result] == [250, 250]


def test_member_with_no_items_pays_no_proportional_tax():
    result = allocate(
        members=[ME, BAO],
        lines=[item(1, 1000), item(2, 100, "tax")],
        assignments={1: [ME]},
        charges={2: PROPORTIONAL},
        total_cents=1100,
    )

    assert {s.member: s.total_cents for s in result} == {ME: 1100, BAO: 0}


def test_total_within_two_cents_of_the_lines_is_split_exactly():
    """reconcile() allows ±2 cents; the shares still add up to the printed total."""
    result = allocate(
        members=[ME, BAO],
        lines=[item(1, 1000), item(2, 1001)],
        assignments={1: [ME], 2: [BAO]},
        charges={},
        total_cents=2002,
    )

    assert sum(s.total_cents for s in result) == 2002


# --- refusals ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("assignments", "charges", "total", "message"),
    [
        ({}, {}, 1000, "line 1"),  # an item nobody took
        ({1: ["stranger"]}, {}, 1000, "isn't in the split"),
        ({1: []}, {}, 1000, "line 1"),
        ({1: [ME]}, {}, 1500, "add up"),  # the bill doesn't add up
        ({1: [ME]}, {9: EVEN}, 1000, "line 9"),  # mode for a line that isn't a charge
    ],
)
def test_invalid_splits_are_refused(assignments, charges, total, message):
    with pytest.raises(SplitError, match=f"(?i){message}"):
        allocate([ME, BAO], [item(1, 1000)], assignments, charges, total)


def test_charge_without_a_mode_is_refused():
    with pytest.raises(SplitError, match="(?i)line 2"):
        allocate([ME], [item(1, 1000), item(2, 80, "tax")], {1: [ME]}, {}, 1080)


@pytest.mark.parametrize("members", [[], [ME] * 2, [str(i) for i in range(13)]])
def test_group_size_and_unique_members_are_enforced(members):
    with pytest.raises(SplitError):
        allocate(members, [item(1, 100)], {1: members[:1] or [ME]}, {}, 100)


# --- properties -------------------------------------------------------------------------------


@st.composite
def bills(draw):
    members = [f"m{i}" for i in range(draw(st.integers(1, 12)))]
    lines, assignments, charges = [], {}, {}
    for row in range(1, draw(st.integers(1, 25)) + 1):
        kind = draw(st.sampled_from(["item", "item", "item", "discount", "tax", "fee", "tip"]))
        if kind == "discount":
            cents = -draw(st.integers(1, 2_000))
        else:
            cents = draw(st.integers(0, 50_000))
        lines.append(SplitLine(row, kind, cents))
        if kind in ("item", "discount"):
            assignments[row] = draw(st.lists(st.sampled_from(members), min_size=1, unique=True))
        else:
            mode = draw(st.sampled_from(["prop", "even", "one"]))
            charges[row] = {
                "prop": PROPORTIONAL,
                "even": EVEN,
                "one": one_person(draw(st.sampled_from(members))),
            }[mode]
    total = sum(line.amount_cents for line in lines) + draw(st.integers(-2, 2))
    return members, lines, assignments, charges, total


@settings(max_examples=500, deadline=None)
@given(bills())
def test_shares_always_sum_exactly_to_the_total(bill):
    members, lines, assignments, charges, total = bill
    if sum(line.amount_cents for line in lines) == 0:
        return  # nothing to scale against; covered by a refusal elsewhere

    result = allocate(members, lines, assignments, charges, total)

    assert sum(s.total_cents for s in result) == total
    assert [s.member for s in result] == members  # seat order kept


@settings(max_examples=500, deadline=None)
@given(bills())
def test_each_share_is_within_one_cent_of_its_exact_value(bill):
    members, lines, assignments, charges, total = bill
    if sum(line.amount_cents for line in lines) == 0:
        return

    exact = exact_shares(members, lines, assignments, charges, total)
    result = allocate(members, lines, assignments, charges, total)

    assert sum(exact.values()) == total
    for share in result:
        assert abs(Fraction(share.total_cents) - exact[share.member]) < 1


@settings(max_examples=300, deadline=None)
@given(bills())
def test_allocation_is_deterministic(bill):
    members, lines, assignments, charges, total = bill
    if sum(line.amount_cents for line in lines) == 0:
        return

    assert allocate(members, lines, assignments, charges, total) == allocate(
        members, lines, assignments, charges, total
    )


@given(st.integers(1, 12), st.integers(1, 10_000_000))
def test_even_split_differs_by_at_most_one_cent(people, cents):
    members = [f"m{i}" for i in range(people)]

    result = allocate(members, [item(1, cents)], {1: members}, {}, cents)

    totals = [s.total_cents for s in result]
    assert max(totals) - min(totals) <= 1


@given(bills())
def test_one_person_with_everything_pays_the_total(bill):
    members, lines, _, _, total = bill
    if sum(line.amount_cents for line in lines) == 0:
        return
    payer = members[0]
    assignments = {line.row: [payer] for line in lines if line.kind in ("item", "discount")}
    charges = {
        line.row: one_person(payer) for line in lines if line.kind not in ("item", "discount")
    }

    result = allocate(members, lines, assignments, charges, total)

    assert {s.member: s.total_cents for s in result}[payer] == total
