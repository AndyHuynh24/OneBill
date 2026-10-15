"""Settle up a trip: who owes whom, in as few payments as possible, to the exact cent.

Each person's net = what they paid for (the receipts they paid, in full) − what they owe (their
shares). Shares always add up to each receipt, so the nets add up to exactly zero.

Payments are greedy: the person who owes the most pays the person owed the most, the smaller of
the two amounts, until everyone is even. That takes at most people − 1 payments. Ties are broken
by name, so the same balances always give the same list.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Receipt:
    """One split receipt: who paid it, and each person's share in cents (by name)."""

    paid_by: str
    shares: Mapping[str, int]


@dataclass(frozen=True)
class Payment:
    from_name: str
    to_name: str
    amount_cents: int


def balances(receipts: Iterable[Receipt]) -> dict[str, int]:
    """Net cents per person: positive is owed money, negative owes money. Names match whatever
    their case (people are named per receipt); the first spelling seen is kept."""
    spelling: dict[str, str] = {}
    nets: dict[str, int] = {}

    def add(name: str, cents: int) -> None:
        key = name.casefold()
        spelling.setdefault(key, name)
        nets[key] = nets.get(key, 0) + cents

    for receipt in receipts:
        add(receipt.paid_by, sum(receipt.shares.values()))
        for name, cents in receipt.shares.items():
            add(name, -cents)
    return {spelling[key]: cents for key, cents in nets.items()}


def settle(nets: Mapping[str, int]) -> list[Payment]:
    """The fewest payments (greedy) that bring every net to zero."""
    if sum(nets.values()) != 0:
        raise ValueError("Balances must add up to zero.")
    owed = sorted(([n, c] for n, c in nets.items() if c > 0), key=lambda p: (-p[1], p[0]))
    owing = sorted(([n, -c] for n, c in nets.items() if c < 0), key=lambda p: (-p[1], p[0]))
    payments: list[Payment] = []
    i = j = 0
    while i < len(owing) and j < len(owed):
        amount = min(owing[i][1], owed[j][1])
        payments.append(Payment(owing[i][0], owed[j][0], amount))
        owing[i][1] -= amount
        owed[j][1] -= amount
        i += owing[i][1] == 0
        j += owed[j][1] == 0
    return payments
