"""Orders: create, look up, ship."""
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from . import clock, gateway, money

log = logging.getLogger(__name__)


@dataclass
class Order:
    id: int
    customer: str
    total: Decimal
    status: str
    created: datetime
    shipped: datetime | None = None


def _from_row(row) -> Order:
    return Order(row["id"], row["customer"], Decimal(row["total"]), row["status"],
                 datetime.fromisoformat(row["created"]),
                 datetime.fromisoformat(row["shipped"]) if row["shipped"] else None)


def create(conn, customer: str, total: Decimal) -> Order:
    order_id = gateway.insert_order(conn, customer, money.to_cents(total), clock.now())
    log.info("created order %s for %s", order_id, customer)
    return get(conn, order_id)


def get(conn, order_id: int) -> Order | None:
    row = gateway.get_order(conn, order_id)
    return _from_row(row) if row else None


def all(conn) -> list:
    return [_from_row(r) for r in gateway.all_orders(conn)]
