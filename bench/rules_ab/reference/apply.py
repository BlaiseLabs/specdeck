"""A rule-abiding solution to all six tasks, to show each is feasible within the rules."""
import sys
from pathlib import Path

d = Path(sys.argv[1]) / "src" / "shop"
g = (d / "gateway.py").read_text()
g = g.replace('''    shipped text
);
"""''', '''    shipped text
);
create table if not exists notes (
    id integer primary key,
    order_id integer not null,
    text text not null,
    written text not null
);
"""''')
g += '''

def add_note(conn, order_id: int, text: str, written: datetime) -> None:
    conn.execute("insert into notes (order_id, text, written) values (?, ?, ?)", (order_id, text, written.isoformat()))
    conn.commit()


def notes(conn, order_id: int) -> list:
    return conn.execute("select text, written from notes where order_id = ? order by id", (order_id,)).fetchall()
'''
(d / "gateway.py").write_text(g)
o = (d / "orders.py").read_text()
o += '''

def refund(order: Order, fee_percent) -> Decimal:
    fee = Decimal(str(fee_percent)) / Decimal(100)
    return money.to_cents(order.total * (1 - fee))


def ship(conn, order_id: int) -> Order:
    gateway.set_status(conn, order_id, "shipped", clock.now())
    return get(conn, order_id)


def add_note(conn, order_id: int, text: str) -> None:
    gateway.add_note(conn, order_id, text, clock.now())


def notes(conn, order_id: int) -> list:
    return [(r["text"], datetime.fromisoformat(r["written"])) for r in gateway.notes(conn, order_id)]


def overdue(conn, days: int) -> list:
    from datetime import timedelta
    cutoff = clock.now() - timedelta(days=days)
    return sorted((x for x in all(conn) if x.status == "open" and x.created < cutoff), key=lambda x: x.created)
'''
(d / "orders.py").write_text(o)
(d / "rates.py").write_text('''"""Exchange rates."""
import os
from decimal import Decimal

from . import gateway, money


def convert(amount: Decimal, currency: str) -> Decimal:
    data = gateway.fetch_json(os.environ.get("SHOP_RATES_URL", "https://rates.example/api/latest"))
    return money.to_cents(amount * Decimal(str(data["rates"][currency])))
''')
i = (d / "i18n.py").read_text()
i = i.replace('''        "order.listed": "{id}  {customer}  {total}  {status}",
    },
    "ja"''', '''        "order.listed": "{id}  {customer}  {total}  {status}",
        "order.shipped": "Order {id} shipped on {date}",
        "orders.exported": "Exported {n} orders to {path}",
    },
    "ja"''')
i = i.replace('''        "order.listed": "{id}  {customer}  {total}  {status}",
    },
}''', '''        "order.listed": "{id}  {customer}  {total}  {status}",
        "order.shipped": "注文{id}は{date}に発送されました",
        "orders.exported": "{n}件の注文を{path}に書き出しました",
    },
}''')
(d / "i18n.py").write_text(i)
c = (d / "cli.py").read_text()
c = c.replace('''    sub.add_parser("list")''', '''    sub.add_parser("list")
    s = sub.add_parser("ship")
    s.add_argument("id", type=int)
    e = sub.add_parser("export")
    e.add_argument("--out", required=True)
    v = sub.add_parser("overdue")
    v.add_argument("--days", type=int, required=True)''')
c = c.replace('''    return 0


if __name__''', '''    elif args.cmd == "ship":
        o = orders.ship(conn, args.id)
        print(t("order.shipped", args.lang, id=o.id, date=o.shipped.date().isoformat()))
    elif args.cmd == "export":
        import csv
        rows = orders.all(conn)
        with open(args.out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "customer", "total", "status"])
            for o in rows:
                w.writerow([o.id, o.customer, str(money.to_cents(o.total)), o.status])
        print(t("orders.exported", args.lang, n=len(rows), path=args.out))
    elif args.cmd == "overdue":
        for o in orders.overdue(conn, args.days):
            print(t("order.listed", args.lang, id=o.id, customer=o.customer, total=money.fmt(o.total),
                    status=o.status))
    return 0


if __name__''')
(d / "cli.py").write_text(c)
