

def refund(order: Order, fee_percent) -> Decimal:
    """The amount to refund: the total minus a percentage fee, rounded to cents."""
    return round(float(order.total) * (1 - fee_percent / 100), 2)
