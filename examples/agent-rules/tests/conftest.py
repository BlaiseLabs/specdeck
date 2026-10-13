import pytest

from shop import gateway


@pytest.fixture
def conn(tmp_path):
    c = gateway.connect(str(tmp_path / "shop.db"))
    yield c
    c.close()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    p = tmp_path / "cli.db"
    monkeypatch.setenv("SHOP_DB", str(p))
    return p
