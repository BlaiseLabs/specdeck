from decimal import Decimal

from shop import cli, money, orders


def test_create_rounds_to_cents_and_stores_utc(conn):
    o = orders.create(conn, "Aiko", Decimal("10.005"))
    assert o.total == Decimal("10.01")
    assert o.created.utcoffset().total_seconds() == 0
    assert orders.get(conn, o.id) == o


def test_parse_and_format():
    assert money.parse("$12.5") == Decimal("12.50")
    assert money.fmt(Decimal("1234.5")) == "$1,234.50"


def test_cli_create_and_list_in_two_languages(db_path, capsys):
    cli.main(["create", "Aiko", "12.50"])
    cli.main(["--lang", "ja", "list"])
    out = capsys.readouterr().out
    assert "Order 1 created for Aiko: $12.50" in out
    assert "1  Aiko  $12.50  open" in out
