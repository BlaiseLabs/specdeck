"""Hidden acceptance tests. No arm ever sees this file.

Every expected value was derived by hand from the twelve rules, not by running the reference,
so the reference must independently agree with them. Each test name starts with the rule it
checks (test_r4_...), so results can be reported per rule and per difficulty.
"""
from decimal import Decimal

import pytest

from pricing import price_order

D = Decimal


def po(*items, date="2026-06-15", member=False):
    return price_order({"date": date, "member": member,
                        "items": [{"sku": f"S{i}", "category": c, "qty": q, "unit_price": p}
                                  for i, (c, q, p) in enumerate(items)]})


def money(v):
    return D(str(v)).quantize(D("0.01"))


# R1 — subtotal
def test_r1_single_line_subtotal():
    assert money(po(("standard", 3, "19.99"))["subtotal"]) == D("59.97")

def test_r1_multi_line_subtotal():
    assert money(po(("standard", 2, "10.00"), ("standard", 1, "5.50"))["subtotal"]) == D("25.50")


# R2 — bulk 15% at qty >= 100, and 100 itself qualifies
def test_r2_exactly_100_gets_bulk():
    assert money(po(("standard", 100, "1.00"))["discount"]) == D("15.00")

def test_r2_99_gets_no_bulk():
    assert money(po(("standard", 99, "1.00"))["discount"]) == D("0.00")

def test_r2_101_gets_bulk():
    assert money(po(("standard", 101, "1.00"))["discount"]) == D("15.15")


# R3 — clearance never gets bulk
def test_r3_clearance_bulk_line_no_discount():
    assert money(po(("clearance", 200, "1.00"))["discount"]) == D("0.00")

def test_r3_only_the_standard_line_is_discounted():
    assert money(po(("standard", 100, "1.00"), ("clearance", 100, "1.00"))["discount"]) == D("15.00")


# R4 — members +5% on non-clearance, non-gift-card, after bulk
def test_r4_member_gets_five_percent():
    assert money(po(("standard", 1, "100.00"), member=True)["discount"]) == D("5.00")

def test_r4_member_stacks_after_bulk():
    # 100.00 -> bulk 85.00 -> member 80.75; discount 19.25 (not 20.00)
    assert money(po(("standard", 100, "1.00"), member=True)["discount"]) == D("19.25")

def test_r4_member_gets_nothing_on_clearance():
    assert money(po(("clearance", 1, "100.00"), member=True)["discount"]) == D("0.00")


# R5 — 8% before 2026-07-01, 10% from that day
# orders of 60.00 ship free, so a wrong shipping-tax rule cannot leak into these
def test_r5_june_30_is_eight_percent():
    assert money(po(("standard", 1, "60.00"), date="2026-06-30")["tax"]) == D("4.80")

def test_r5_july_1_is_ten_percent():
    assert money(po(("standard", 1, "60.00"), date="2026-07-01")["tax"]) == D("6.00")


# R6 — gift cards never taxed
def test_r6_gift_card_alone_untaxed():
    assert money(po(("gift_card", 1, "50.00"))["tax"]) == D("0.00")

def test_r6_only_the_merchandise_is_taxed():
    assert money(po(("gift_card", 1, "50.00"), ("standard", 1, "10.00"))["tax"]) == D("0.80")


# R7 — tax on the discounted amount
def test_r7_tax_after_member_discount():
    # 100.00 -> 95.00 after member 5%; 8% of 95.00 = 7.60 (not 8.00)
    assert money(po(("standard", 1, "100.00"), member=True)["tax"]) == D("7.60")


# R8 — 5.99 flat, free at >= 50.00 after discounts
def test_r8_just_under_threshold_pays():
    assert money(po(("standard", 1, "49.99"))["shipping"]) == D("5.99")

def test_r8_exactly_threshold_is_free():
    assert money(po(("standard", 1, "50.00"))["shipping"]) == D("0.00")

def test_r8_threshold_is_after_discounts():
    # 52.00 -> 49.40 after member 5%: under 50, so shipping is charged
    assert money(po(("standard", 1, "52.00"), member=True)["shipping"]) == D("5.99")


# R9 — shipping never taxed
def test_r9_shipping_not_in_tax():
    r = po(("standard", 1, "10.00"))
    assert money(r["tax"]) == D("0.80") and money(r["total"]) == D("16.79")


# R10 — digital-only orders ship free
def test_r10_digital_only_ships_free():
    assert money(po(("digital", 1, "9.99"))["shipping"]) == D("0.00")

def test_r10_mixed_digital_and_physical_pays():
    assert money(po(("digital", 1, "9.99"), ("standard", 1, "5.00"))["shipping"]) == D("5.99")


# R11 — half-up rounding per component; total is the exact sum of the parts
def test_r11_tax_rounds_half_up():
    # 51.25 * 10% = 5.125 -> 5.13 (banker's rounding gives 5.12); ships free, so isolated
    assert money(po(("standard", 1, "51.25"), date="2026-07-01")["tax"]) == D("5.13")

def test_r11_discount_rounds_half_up():
    # member 5% of 0.10 = 0.005 -> 0.01
    assert money(po(("standard", 1, "0.10"), member=True)["discount"]) == D("0.01")

@pytest.mark.parametrize("order", [
    (("standard", 3, "19.99"),),
    (("standard", 100, "1.00"), ("clearance", 7, "3.33")),
    (("standard", 1, "1.25"), ("gift_card", 1, "20.00")),
])
def test_r11_total_is_sum_of_parts(order):
    r = po(*order, member=True, date="2026-07-02")
    parts = money(r["subtotal"]) - money(r["discount"]) + money(r["tax"]) + money(r["shipping"])
    assert money(r["total"]) == parts


# R12 — gift cards never discounted
def test_r12_bulk_gift_cards_not_discounted():
    assert money(po(("gift_card", 100, "1.00"))["discount"]) == D("0.00")

def test_r12_member_gift_card_not_discounted():
    assert money(po(("gift_card", 1, "50.00"), member=True)["discount"]) == D("0.00")
