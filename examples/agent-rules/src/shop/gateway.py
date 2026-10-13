"""Storage and the outside world: the SQLite database and HTTP."""
import json
import os
import sqlite3
import urllib.request
from datetime import datetime
from decimal import Decimal

SCHEMA = """
create table if not exists orders (
    id integer primary key,
    customer text not null,
    total text not null,          -- Decimal as text, never REAL
    status text not null default 'open',
    created text not null,        -- ISO 8601, UTC
    shipped text
);
"""


def connect(path: str = "") -> sqlite3.Connection:
    conn = sqlite3.connect(path or os.environ.get("SHOP_DB", "shop.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_order(conn, customer: str, total: Decimal, created: datetime) -> int:
    cur = conn.execute("insert into orders (customer, total, created) values (?, ?, ?)",
                       (customer, str(total), created.isoformat()))
    conn.commit()
    return cur.lastrowid


def get_order(conn, order_id: int):
    return conn.execute("select * from orders where id = ?", (order_id,)).fetchone()


def all_orders(conn) -> list:
    return conn.execute("select * from orders order by id").fetchall()


def set_status(conn, order_id: int, status: str, shipped: datetime | None = None) -> None:
    conn.execute("update orders set status = ?, shipped = ? where id = ?",
                 (status, shipped.isoformat() if shipped else None, order_id))
    conn.commit()


def fetch_json(url: str, timeout: float = 10) -> dict:
    """GET a URL and parse its JSON body. Numbers with a fraction are read as Decimal."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"), parse_float=Decimal)
