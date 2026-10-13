"""Money amounts. Amounts are Decimal values in dollars, rounded to cents."""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def to_cents(amount: Decimal) -> Decimal:
    """Round an amount to whole cents, half up."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def parse(text: str) -> Decimal:
    """An amount typed by a person, such as "12.5" or "$12.50"."""
    return to_cents(Decimal(text.strip().lstrip("$")))


def fmt(amount: Decimal) -> str:
    return f"${to_cents(amount):,}"
