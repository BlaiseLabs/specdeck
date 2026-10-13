"""The `shop` command line."""
import argparse
import sys

from . import gateway, money, orders
from .i18n import t


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="shop")
    ap.add_argument("--lang", default="en", choices=["en", "ja"])
    ap.add_argument("--db", default="")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("customer")
    c.add_argument("total")
    sub.add_parser("list")
    args = ap.parse_args(argv)
    conn = gateway.connect(args.db)

    if args.cmd == "create":
        o = orders.create(conn, args.customer, money.parse(args.total))
        print(t("order.created", args.lang, id=o.id, customer=o.customer, total=money.fmt(o.total)))
    elif args.cmd == "list":
        for o in orders.all(conn):
            print(t("order.listed", args.lang, id=o.id, customer=o.customer, total=money.fmt(o.total),
                    status=o.status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
