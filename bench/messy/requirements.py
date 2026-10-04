"""Ground truth for the messy-material experiment. Frozen before any material or arm exists.

Twelve rules for pricing an online-shop order. Each is tagged with how hard it should be to
find in the messy sources, because the question is not "can a model price an order" but
"does a spec step help it *find* rules that are scattered, buried, contradicted or only
implied" — the synthesis job a real spec does and today's clean-docstring tests never asked.
"""

REQUIREMENTS = [
    # id    difficulty         rule
    ("R1",  "plain",           "Subtotal is the sum of quantity times unit price over all lines."),
    ("R2",  "contradicted",    "Lines with quantity of 100 or more get a 15% bulk discount. 100 itself qualifies "
                               "(an early meeting said 'over 100'; a later message corrects it to '100 or more')."),
    ("R3",  "buried",          "Clearance lines never get the bulk discount."),
    ("R4",  "buried",          "Loyalty members get an extra 5% off non-clearance, non-gift-card lines, applied "
                               "after any bulk discount (so a bulk line for a member costs 0.85 * 0.95)."),
    ("R5",  "dated",           "Tax is 8% for orders dated before 2026-07-01 and 10% for orders on or after it."),
    ("R6",  "buried",          "Gift cards are never taxed."),
    ("R7",  "plain",           "Tax is charged on the amount after discounts, not before."),
    ("R8",  "plain",           "Shipping is a flat 5.99, free when the order total after discounts is 50.00 or more."),
    ("R9",  "buried",          "Shipping is never taxed."),
    ("R10", "implied",         "An order containing only digital items ships free. Nobody states this; the sample "
                               "data shows it."),
    ("R11", "buried",          "Discount and tax are each rounded half-up to the cent; the total is the exact sum of "
                               "subtotal - discount + tax + shipping."),
    ("R12", "buried",          "Gift cards are never discounted, neither by bulk nor by membership."),
]

INTERFACE = '''\
Implement this exact function in a module named `pricing.py`:

```python
def price_order(order: dict) -> dict:
    """
    order = {
        "date": "2026-07-15",          # ISO date the order was placed
        "member": True,                # loyalty member or not
        "items": [
            {"sku": "A1", "category": "standard", "qty": 3, "unit_price": "19.99"},
        ],
    }
    category is one of: "standard", "clearance", "gift_card", "digital".
    unit_price is a decimal string.

    Return {"subtotal", "discount", "tax", "shipping", "total"} as decimal.Decimal
    values with exactly two decimal places. "discount" is a positive amount taken off.
    """
```
'''
