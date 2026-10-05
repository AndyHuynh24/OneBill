"""Split a receipt between people, to the exact cent.

Every person's share of every line is computed exactly (as a fraction) first:
- an item or discount is split evenly among the people it's assigned to;
- a proportional charge (tax, fee, tip) is weighted by each person's items after discounts;
- an even charge is split equally among everyone;
- a one-person charge goes entirely to that person;
- a group charge is split equally among the people chosen (like an item).

Then rounding happens once, over the whole bill (largest remainder): everyone gets the floor in
cents and the leftover cents go to the largest fractional parts, ties broken by seat order.
The shares therefore always sum to the target (the printed total, or the recorded lines when a
misread total doesn't match them), and each is within a cent of exact.
"""

import math
from dataclasses import dataclass, field
from fractions import Fraction

ITEM_KINDS = frozenset({"item", "discount"})
CHARGE_KINDS = frozenset({"tax", "fee", "tip"})
MAX_MEMBERS = 12
TOLERANCE_CENTS = 2  # matches reconcile(): the printed total may differ from the lines by ≤ 2¢


class SplitError(ValueError):
    """The split can't be computed as requested; the message is safe to show to the user."""


@dataclass(frozen=True)
class SplitLine:
    row: int
    kind: str
    amount_cents: int


@dataclass(frozen=True)
class ChargeMode:
    mode: str  # "proportional" | "even" | "person" | "people"
    member: str | None = None
    members: tuple[str, ...] = ()


PROPORTIONAL = ChargeMode("proportional")
EVEN = ChargeMode("even")


def one_person(member: str) -> ChargeMode:
    return ChargeMode("person", member)


def some_people(members: list[str]) -> ChargeMode:
    """A charge shared equally by the people chosen, like an item."""
    return ChargeMode("people", members=tuple(members))


@dataclass(frozen=True)
class Share:
    member: str
    total_cents: int
    items_cents: int  # what they ordered (display); extras = tax/fees/tip share
    extras_cents: int
    rows: list[int] = field(default_factory=list)


def split_target(
    lines: list[SplitLine], total_cents: int, tax_included: bool = False
) -> tuple[int, bool]:
    """What the shares add up to, and whether that's the printed total.

    The printed total when the lines agree with it (±2¢); otherwise the recorded lines, so a
    misread total never blocks a split (the summary says it differs from the receipt)."""
    lines_total = sum(line.amount_cents for line in _split_lines(lines, tax_included))
    if abs(total_cents - lines_total) <= TOLERANCE_CENTS:
        return total_cents, True
    return lines_total, False


def _split_lines(lines: list[SplitLine], tax_included: bool) -> list[SplitLine]:
    """Items, discounts, fees, tip, and tax — unless the prices already include the tax."""
    kinds = (ITEM_KINDS | CHARGE_KINDS) - ({"tax"} if tax_included else set())
    return [line for line in lines if line.kind in kinds]


def allocate(
    members: list[str],
    lines: list[SplitLine],
    assignments: dict[int, list[str]],
    charges: dict[int, ChargeMode],
    total_cents: int,
    tax_included: bool = False,
) -> list[Share]:
    """Per-person shares in seat order, summing exactly to `split_target(...)`: the printed
    total when the lines agree with it, otherwise the recorded lines."""
    target, _ = split_target(lines, total_cents, tax_included)
    exact = exact_shares(members, lines, assignments, charges, total_cents, tax_included)
    totals = _largest_remainder(members, exact, target)

    shares = []
    for member in members:
        rows = sorted(
            row for row, people in assignments.items() if member in people and row in _rows(lines)
        )
        items = _items_exact(member, lines, assignments)
        items_cents = math.floor(items + Fraction(1, 2))  # display only; extras absorb rounding
        shares.append(
            Share(member, totals[member], items_cents, totals[member] - items_cents, rows)
        )
    return shares


def exact_shares(
    members: list[str],
    lines: list[SplitLine],
    assignments: dict[int, list[str]],
    charges: dict[int, ChargeMode],
    total_cents: int,
    tax_included: bool = False,
) -> dict[str, Fraction]:
    """Each person's exact share, scaled so the shares sum to the split target."""
    split_lines = _split_lines(lines, tax_included)
    _validate(members, split_lines, assignments, charges)
    lines_total = sum(line.amount_cents for line in split_lines)
    target, _ = split_target(lines, total_cents, tax_included)

    exact = {m: _items_exact(m, split_lines, assignments) for m in members}
    weights = {m: max(Fraction(0), exact[m]) for m in members}  # a lone discount isn't a weight
    weight_total = sum(weights.values())
    for line in split_lines:
        if line.kind not in CHARGE_KINDS:
            continue
        mode = charges[line.row]
        if mode.mode == "person":
            exact[mode.member] += line.amount_cents
        elif mode.mode == "people":
            for m in mode.members:
                exact[m] += Fraction(line.amount_cents, len(mode.members))
        elif mode.mode == "proportional" and weight_total > 0:
            for m in members:
                exact[m] += line.amount_cents * weights[m] / weight_total
        else:  # even, or proportional when nobody has items
            for m in members:
                exact[m] += Fraction(line.amount_cents, len(members))

    if lines_total == 0 or target == lines_total:
        return exact
    # Within tolerance of the printed total: spread the cent or two fairly.
    scale = Fraction(target, lines_total)
    return {m: v * scale for m, v in exact.items()}


def _items_exact(
    member: str, lines: list[SplitLine], assignments: dict[int, list[str]]
) -> Fraction:
    total = Fraction(0)
    for line in lines:
        people = assignments.get(line.row, [])
        if line.kind in ITEM_KINDS and member in people:
            total += Fraction(line.amount_cents, len(people))
    return total


def _largest_remainder(
    members: list[str], exact: dict[str, Fraction], total_cents: int
) -> dict[str, int]:
    floors = {m: math.floor(exact[m]) for m in members}
    leftover = total_cents - sum(floors.values())
    # Largest fractional part first; ties go to the earlier seat.
    order = sorted(
        range(len(members)), key=lambda i: (-(exact[members[i]] - floors[members[i]]), i)
    )
    for i in order[:leftover]:
        floors[members[i]] += 1
    return floors


def _rows(lines: list[SplitLine]) -> set[int]:
    return {line.row for line in lines}


def _validate(
    members: list[str],
    lines: list[SplitLine],
    assignments: dict[int, list[str]],
    charges: dict[int, ChargeMode],
) -> None:
    if not 1 <= len(members) <= MAX_MEMBERS:
        raise SplitError(f"A split needs between 1 and {MAX_MEMBERS} people.")
    if len(set(members)) != len(members):
        raise SplitError("Each person can only be in the split once.")
    known = set(members)
    kinds = {line.row: line.kind for line in lines}

    for row, people in assignments.items():
        if kinds.get(row) not in ITEM_KINDS:
            raise SplitError(f"Line {row} isn't an item that can be assigned to people.")
        if not people:
            raise SplitError(f"Choose who had line {row}.")
        if set(people) - known:
            raise SplitError(f"Line {row} is assigned to someone who isn't in the split.")
        if len(set(people)) != len(people):
            raise SplitError(f"Line {row} lists the same person twice.")
    for row, mode in charges.items():
        if kinds.get(row) not in CHARGE_KINDS:
            raise SplitError(f"Line {row} isn't a tax, fee, or tip.")
        if mode.mode == "person" and mode.member not in known:
            raise SplitError(f"Line {row} is assigned to someone who isn't in the split.")
        if mode.mode == "people" and (
            not mode.members
            or set(mode.members) - known
            or len(set(mode.members)) != len(mode.members)
        ):
            raise SplitError(f"Choose who shares line {row} (people in the split, once each).")
        if mode.mode not in ("proportional", "even", "person", "people"):
            raise SplitError(f"Choose how to split line {row}.")
    for row, kind in kinds.items():
        if kind in ITEM_KINDS and row not in assignments:
            raise SplitError(f"Choose who had line {row}.")
        if kind in CHARGE_KINDS and row not in charges:
            raise SplitError(f"Choose who pays line {row}.")
