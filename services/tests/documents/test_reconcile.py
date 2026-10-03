"""The "understanding" tests: summary, payment, and change lines must never be summed as items.

Each case is a receipt as the model might label it, including the mistakes we must survive.
"""

from documents.reconcile import Line, reconcile


def lines(*rows: tuple[str, str, int]) -> list[Line]:
    """Build Line objects from (description, kind, amount_cents) tuples, numbering rows in order."""
    return [
        Line(row=i + 1, description=d, kind=k, amount_cents=a) for i, (d, k, a) in enumerate(rows)
    ]


GROCERY_CORRECT = lines(
    ("BANANAS", "item", 129),
    ("MILK 1 GAL", "item", 419),
    ("BREAD", "item", 350),
    ("EGGS", "item", 550),
    ("CHICKEN", "item", 1200),
    ("COUPON", "discount", -200),
    ("SUBTOTAL", "subtotal", 2448),
    ("TAX", "tax", 170),
    ("TOTAL", "total", 2618),
    ("CASH", "payment", 3000),
    ("CHANGE", "change", 382),
)


def test_correctly_labelled_receipt_reconciles():
    r = reconcile(GROCERY_CORRECT)

    assert r.ok
    assert r.total_cents == 2618
    assert r.items_cents == 2648
    assert r.discounts_cents == -200
    assert r.tax_cents == 170
    assert r.issues == []
    assert r.corrections == []


def test_total_subtotal_cash_change_mislabelled_as_items_are_corrected():
    """Andy's bug: the model calls every money line an 'item'. The total must still be $26.18."""
    mislabelled = lines(
        ("BANANAS", "item", 129),
        ("MILK 1 GAL", "item", 419),
        ("BREAD", "item", 350),
        ("EGGS", "item", 550),
        ("CHICKEN", "item", 1200),
        ("COUPON", "discount", -200),
        ("SUBTOTAL", "item", 2448),
        ("TAX", "item", 170),
        ("TOTAL", "item", 2618),
        ("CASH", "item", 3000),
        ("CHANGE", "item", 382),
    )

    r = reconcile(mislabelled)

    assert r.ok, r.issues
    assert r.total_cents == 2618
    assert r.items_cents == 2648
    assert {(c.row, c.to_kind) for c in r.corrections} == {
        (7, "subtotal"),
        (8, "tax"),
        (9, "total"),
        (10, "payment"),
        (11, "change"),
    }


def test_textract_summary_values_confirm_a_mislabelled_total():
    """No other line carries the printed total, but Textract's TOTAL summary field does."""
    r = reconcile(
        lines(("SANDWICH", "item", 899), ("SODA", "item", 249), ("TOTAL", "item", 1148)),
        textract_summary={"TOTAL": 1148},
    )

    assert r.ok
    assert r.total_cents == 1148
    assert r.items_cents == 1148


def test_item_whose_name_contains_a_keyword_is_not_reclassified():
    """'TOTAL WIRELESS' is a product. Its amount doesn't match any printed summary value."""
    r = reconcile(
        lines(
            ("TOTAL WIRELESS CARD", "item", 2500),
            ("CASHEWS", "item", 699),
            ("SUBTOTAL", "subtotal", 3199),
            ("TAX", "tax", 0),
            ("TOTAL", "total", 3199),
        )
    )

    assert r.ok
    assert r.corrections == []
    assert r.items_cents == 3199


def test_card_payment_equal_to_total_reconciles():
    r = reconcile(
        lines(
            ("COFFEE", "item", 450),
            ("MUFFIN", "item", 325),
            ("TAX", "tax", 62),
            ("TOTAL", "total", 837),
            ("VISA ****1234", "payment", 837),
        )
    )

    assert r.ok
    assert r.total_cents == 837
    assert r.payment_cents == 837
    assert r.change_cents == 0


def test_repeated_total_line_is_not_a_conflict():
    r = reconcile(
        lines(
            ("PASTA", "item", 1500),
            ("TOTAL", "total", 1500),
            ("AMOUNT TENDERED", "payment", 2000),
            ("CHANGE DUE", "change", 500),
            ("TOTAL", "total", 1500),
        )
    )

    assert r.ok
    assert r.total_cents == 1500


def test_restaurant_slip_with_pre_tip_and_final_total():
    r = reconcile(
        lines(
            ("BURGER", "item", 1600),
            ("FRIES", "item", 500),
            ("SUBTOTAL", "subtotal", 2100),
            ("TAX", "tax", 189),
            ("TOTAL", "total", 2289),
            ("TIP", "tip", 400),
            ("TOTAL", "total", 2689),
        )
    )

    assert r.ok, r.issues
    assert r.tip_cents == 400
    assert r.total_cents == 2689


def test_multiple_tax_lines_are_summed():
    r = reconcile(
        lines(
            ("WIDGET", "item", 1000),
            ("STATE TAX", "tax", 60),
            ("CITY TAX", "tax", 25),
            ("TOTAL", "total", 1085),
        )
    )

    assert r.ok
    assert r.tax_cents == 85


def test_fee_outside_subtotal():
    r = reconcile(
        lines(
            ("GROCERIES", "item", 2000),
            ("SUBTOTAL", "subtotal", 2000),
            ("BAG FEE", "fee", 10),
            ("TAX", "tax", 160),
            ("TOTAL", "total", 2170),
        )
    )

    assert r.ok, r.issues
    assert r.fees_cents == 10


def test_fee_included_in_subtotal():
    r = reconcile(
        lines(
            ("GROCERIES", "item", 2000),
            ("BAG FEE", "fee", 10),
            ("SUBTOTAL", "subtotal", 2010),
            ("TAX", "tax", 160),
            ("TOTAL", "total", 2170),
        )
    )

    assert r.ok, r.issues


def test_savings_recap_line_is_info_not_a_second_discount():
    """'YOU SAVED $2.00' repeats the coupon. If it were counted again, the math would break."""
    r = reconcile(
        lines(
            ("SHAMPOO", "item", 899),
            ("COUPON", "discount", -200),
            ("SUBTOTAL", "subtotal", 699),
            ("TOTAL", "total", 699),
            ("YOU SAVED", "discount", 200),  # model mistake: recap labelled as a discount
        )
    )

    assert r.ok, r.issues
    assert r.discounts_cents == -200
    assert any(c.row == 5 and c.to_kind == "info" for c in r.corrections)


def test_positive_discount_amount_is_treated_as_negative():
    r = reconcile(
        lines(
            ("JACKET", "item", 5000),
            ("MEMBER DISCOUNT", "discount", 500),
            ("SUBTOTAL", "subtotal", 4500),
            ("TOTAL", "total", 4500),
        )
    )

    assert r.ok
    assert r.discounts_cents == -500


def test_rounding_within_two_cents_is_accepted():
    r = reconcile(
        lines(("A", "item", 333), ("B", "item", 333), ("C", "item", 333), ("TOTAL", "total", 1000))
    )

    assert r.ok
    assert r.total_cents == 1000


def test_mismatch_beyond_tolerance_needs_review_with_readable_reason():
    """Textract missed an item: items sum to $9.00 but the printed subtotal is $12.00."""
    r = reconcile(
        lines(
            ("A", "item", 400),
            ("B", "item", 500),
            ("SUBTOTAL", "subtotal", 1200),
            ("TAX", "tax", 0),
            ("TOTAL", "total", 1200),
        )
    )

    assert not r.ok
    assert r.total_cents == 1200  # the printed total is still reported, never replaced
    assert any("Items add up to $9.00" in i and "$12.00" in i for i in r.issues)


def test_payment_mismatch_is_a_note_when_the_bill_itself_adds_up():
    """The items/subtotal/total arithmetic already confirms the total; a confusing payment
    section (misread change, dual-pricing offer) shouldn't send the document to review."""
    r = reconcile(
        lines(
            ("A", "item", 1000),
            ("TOTAL", "total", 1000),
            ("CASH", "payment", 2000),
            ("CHANGE", "change", 500),  # should be 10.00: a misread
        )
    )

    assert r.ok
    assert r.total_cents == 1000
    assert any("Paid $20.00 minus change $5.00" in n for n in r.notes)


def test_amount_applied_plus_tendered_and_change_reconciles():
    """receipt10: "Cash 58.40" (applied), "Total Tender 60.00" (handed over), "Change 1.60"."""
    r = reconcile(
        lines(
            ("FRENCH TOAST", "item", 5840),
            ("TOTAL", "total", 5840),
            ("CASH", "payment", 5840),
            ("TOTAL TENDER", "payment", 6000),
            ("CHANGE", "change", 160),
        )
    )

    assert r.ok
    assert r.notes == []


def test_dual_pricing_offer_does_not_block():
    """receipt4: "Pay With Cash 55.49" is a cash-price offer printed under the real total."""
    r = reconcile(
        lines(
            ("TACOS", "item", 5270),
            ("SUBTOTAL", "subtotal", 5270),
            ("SALES TAX", "tax", 501),
            ("TOTAL", "total", 5771),
            ("PAY WITH CASH", "payment", 5549),
        )
    )

    assert r.ok
    assert r.total_cents == 5771


def test_tax_recap_and_repeated_charge_are_recognized_as_info():
    """receipt9: tax breakdown printed after the total, and the gratuity printed twice."""
    r = reconcile(
        lines(
            ("PASTA", "item", 49950),
            ("20% GRATUITY", "tip", 9990),
            ("SUBTOTAL", "subtotal", 49950),
            ("SALES TAX", "tax", 3374),
            ("SERVICE CHRG", "fee", 9990),
            ("TOTAL", "total", 63314),
            ("SALES TAX", "tax", 2244),
            ("LIQUOR TAX", "tax", 1130),
        )
    )

    assert r.ok, r.issues
    assert r.tax_cents == 3374
    assert r.tip_cents == 9990
    assert r.fees_cents == 0
    relabelled = {(c.row, c.to_kind) for c in r.corrections}
    assert relabelled == {(5, "info"), (7, "info"), (8, "info")}


def test_overage_rule_never_touches_purchased_items():
    r = reconcile(
        lines(
            ("A", "item", 1000),
            ("B", "item", 500),  # total says only A was paid: genuinely doesn't add up
            ("TOTAL", "total", 1000),
        )
    )

    assert not r.ok
    assert r.corrections == []


def test_bill_with_amount_due_and_no_total_line():
    r = reconcile(
        lines(
            ("ELECTRIC SERVICE", "item", 7210),
            ("TAXES AND FEES", "tax", 1200),
            ("AMOUNT DUE", "balance_due", 8410),
        )
    )

    assert r.ok, r.issues
    assert r.total_cents == 8410


def test_no_printed_total_needs_review():
    r = reconcile(lines(("A", "item", 500), ("B", "item", 700)))

    assert not r.ok
    assert r.total_cents is None
    assert any("No total" in i for i in r.issues)


def test_conflicting_totals_need_review():
    r = reconcile(
        lines(
            ("A", "item", 1000),
            ("TOTAL", "total", 1000),
            ("TOTAL", "total", 1300),  # not explained by a tip line
        )
    )

    assert not r.ok


def test_suspect_rows_point_at_lines_that_explain_a_mismatch():
    """Feeds the targeted repair prompt: 'rows 4 look like summary lines'."""
    r = reconcile(
        lines(
            ("A", "item", 1000),
            ("B", "item", 500),
            ("SUBTOTAL", "subtotal", 1500),
            ("GRAND TTL", "item", 1500),  # odd OCR text the keyword list doesn't know
            ("TOTAL", "total", 1500),
        )
    )

    assert not r.ok
    assert 4 in r.suspect_rows


def test_documents_without_totals_can_opt_out_of_the_total_check():
    """Warranties and leases have no total: missing one isn't an error for them."""
    r = reconcile(lines(), total_required=False)

    assert r.ok
    assert r.total_cents is None


def test_tax_inclusive_prices_add_up_to_the_total():
    """KFC Hanoi: item prices include VAT. Items = total; subtotal (pre-tax) + tax = total."""
    r = reconcile(
        lines(
            ("C1 CBO GA RAN", "item", 8700000),
            ("PEACH TEA", "item", 2400000),
            ("CONE VANI", "item", 1800000),
            ("OFFLINE_DIS30K", "discount", -3000000),
            ("SUBTOTAL", "subtotal", 9166667),
            ("TOTAL TAX", "tax", 733333),
            ("EAT IN TOTAL", "total", 9900000),
            ("CASH", "payment", 10000000),
            ("CHANGE", "change", 100000),
        )
    )

    assert r.ok, r.issues
    assert r.tax_included
    assert r.total_cents == 9900000


def test_tax_inclusive_without_a_printed_subtotal():
    r = reconcile(
        lines(
            ("PHO BO", "item", 6000000),
            ("TRA DA", "item", 500000),
            ("VAT 8% (INCLUDED)", "tax", 481481),
            ("TOTAL", "total", 6500000),
        )
    )

    assert r.ok, r.issues
    assert r.tax_included


def test_a_us_receipt_is_still_tax_added():
    r = reconcile(
        lines(
            ("BANANAS", "item", 1000),
            ("SUBTOTAL", "subtotal", 1000),
            ("TAX", "tax", 80),
            ("TOTAL", "total", 1080),
        )
    )

    assert r.ok
    assert not r.tax_included


def test_items_matching_the_total_without_any_tax_is_not_mistaken_for_tax_inclusive():
    r = reconcile(
        lines(
            ("A", "item", 1000),
            ("SUBTOTAL", "subtotal", 900),  # genuinely inconsistent
            ("TOTAL", "total", 1000),
        )
    )

    assert not r.ok
    assert not r.tax_included


def test_a_crossed_out_original_listed_as_a_second_item_is_dropped():
    """Walmart: Reese's Puffs $4.87 (sale) and $6.47 (crossed out), "$1.60 from savings"."""
    r = reconcile(
        lines(
            ("REESES PUFFS", "item", 487),
            ("Reeses Puffs", "item", 647),
            ("EGGS", "item", 393),
            ("SUBTOTAL", "subtotal", 880),
            ("TOTAL", "total", 880),
        ),
        savings={160},
    )

    assert r.ok, r.issues
    assert r.items_cents == 880
    assert [(c.row, c.to_kind) for c in r.corrections] == [(2, "info")]


def test_without_a_printed_saving_identical_items_are_both_kept():
    r = reconcile(
        lines(("AXE", "item", 396), ("AXE", "item", 498), ("TOTAL", "total", 894)),
        savings=set(),
    )

    assert r.ok
    assert r.items_cents == 894
