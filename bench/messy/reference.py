"""The correct implementation of the twelve rules. Exists only to prove the hidden tests are
right: every acceptance test must pass against this, and must fail against a plausible
implementation that misses the rule it is testing."""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def _r(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def price_order(order: dict) -> dict:
    items = order.get("items") or []
    member = bool(order.get("member"))
    rate = Decimal("0.10") if order.get("date", "") >= "2026-07-01" else Decimal("0.08")
    subtotal = Decimal("0")
    discount = Decimal("0")
    taxable = Decimal("0")
    for it in items:
        line = Decimal(str(it["unit_price"])) * int(it["qty"])
        subtotal += line
        cat = it["category"]
        after = line
        if cat != "gift_card":
            if it["qty"] >= 100 and cat != "clearance":
                after = after * Decimal("0.85")
            if member and cat != "clearance":
                after = after * Decimal("0.95")
        discount += line - after
        if cat != "gift_card":
            taxable += after
    discount = _r(discount)
    tax = _r(taxable * rate)
    net = subtotal - discount
    digital_only = bool(items) and all(i["category"] == "digital" for i in items)
    if not items or digital_only or net >= Decimal("50.00"):
        shipping = Decimal("0.00")
    else:
        shipping = Decimal("5.99")
    return {"subtotal": _r(subtotal), "discount": discount, "tax": tax,
            "shipping": shipping, "total": _r(subtotal - discount + tax + shipping)}
