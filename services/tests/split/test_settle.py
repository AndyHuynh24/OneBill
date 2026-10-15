import pytest

from split.settle import Payment, Receipt, balances, settle


def receipt(paid_by, **shares):
    return Receipt(paid_by=paid_by, shares=shares)


def test_whoever_paid_is_owed_everyone_elses_shares():
    nets = balances([receipt("Me", Me=1000, Minh=2000, Linh=1500)])

    assert nets == {"Me": 3500, "Minh": -2000, "Linh": -1500}


def test_balances_add_up_across_receipts_and_always_sum_to_zero():
    nets = balances(
        [
            receipt("Me", Me=1000, Minh=2000, Linh=1500),
            receipt("Minh", Me=3000, Minh=3000, Linh=3000),
            receipt("Linh", Me=500, Linh=500),
        ]
    )

    assert nets == {"Me": 3500 - 3000 - 500, "Minh": -2000 + 6000, "Linh": -1500 - 3000 + 500}
    assert sum(nets.values()) == 0


def test_names_match_whatever_their_case_and_keep_the_first_spelling():
    nets = balances([receipt("Me", Me=100, minh=200), receipt("MINH", Minh=50, Me=50)])

    assert nets == {"Me": 150, "minh": -150}


def test_fewest_payments_settle_everyone():
    nets = {"Me": 5000, "Minh": -3000, "Linh": -1500, "Bao": -500}

    payments = settle(nets)

    assert payments == [
        Payment("Minh", "Me", 3000),
        Payment("Linh", "Me", 1500),
        Payment("Bao", "Me", 500),
    ]


def test_payments_can_flow_between_friends():
    payments = settle({"Me": 2000, "Minh": 1000, "Linh": -2500, "Bao": -500})

    assert payments == [
        Payment("Linh", "Me", 2000),
        Payment("Linh", "Minh", 500),
        Payment("Bao", "Minh", 500),
    ]
    assert len(payments) <= 3  # never more than people − 1


def test_every_payment_together_makes_everyone_even():
    nets = balances(
        [
            receipt("Me", Me=1234, Minh=2345, Linh=3456, Bao=987),
            receipt("Bao", Me=1111, Minh=999, Linh=1, Bao=4000),
            receipt("Linh", Me=700, Minh=700, Linh=700),
        ]
    )

    after = dict(nets)
    for p in settle(nets):
        after[p.from_name] += p.amount_cents
        after[p.to_name] -= p.amount_cents

    assert set(after.values()) == {0}


def test_nobody_owes_anything_when_everyone_paid_their_own_way():
    assert settle(balances([receipt("Me", Me=1000), receipt("Minh", Minh=800)])) == []


def test_ties_are_settled_in_a_stable_order():
    first = settle({"Me": 1000, "Minh": 1000, "Linh": -1000, "Bao": -1000})

    assert first == settle({"Bao": -1000, "Linh": -1000, "Minh": 1000, "Me": 1000})


def test_balances_that_dont_sum_to_zero_are_refused():
    with pytest.raises(ValueError, match="add up to zero"):
        settle({"Me": 100, "Minh": -50})
