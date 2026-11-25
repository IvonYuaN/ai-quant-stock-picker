"""持仓账本损坏必须 fail-closed：绝不能渲染成「0 持仓」，更不能被覆盖。

历史行为：`portfolio._load()` 把 `JSONDecodeError` 和 `FileNotFoundError` 一起
吞成 `{"holdings": []}`。于是账本一损坏，界面就显示「没有持仓」；更致命的是
`get_portfolio()` 回填名称时会 `_load()` 再 `_save()` —— 一次普通的行情刷新就能
把空账本原子改名覆盖掉真实仓位，用户的钱包记录永久消失且全程无报错。

本文件的用例锁住三件事：
1. 损坏 ⇒ 抛 `PortfolioCorrupt`（不是空列表）；
2. 损坏 ⇒ 任何写路径都不会落盘（文件字节级不变）；
3. 对照组：文件真的不存在 ⇒ 仍然是空持仓（三态可区分，不能把正常空账本也说成损坏）。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app as app_module
import portfolio as pf

client = TestClient(app_module.app)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """把持仓目录指到 tmp，并禁用一切行情网络请求。"""
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(pf, "_tenant_id", lambda: "local")

    def _no_network(codes):
        raise AssertionError(f"损坏路径不该打行情源：{codes}")

    monkeypatch.setattr(pf.astock, "tencent_quote", _no_network)
    return tmp_path / "portfolio.json"


def _write(path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_missing_ledger_is_really_empty(store):
    """对照组：文件不存在 = 真的没有持仓 ⇒ 空列表，不抛错。"""
    assert not store.exists()
    data = pf.get_portfolio()
    assert data["holdings"] == []
    assert data["totals"]["market_value"] == 0


def test_corrupt_ledger_raises_instead_of_empty_holdings(store):
    _write(store, "{broken")
    with pytest.raises(pf.PortfolioCorrupt) as exc:
        pf.get_portfolio()
    message = str(exc.value)
    assert "不代表你没有持仓" in message
    assert str(store) in message, "错误里要带上文件路径，否则用户无从修复"


def test_non_dict_ledger_raises(store):
    _write(store, "[1, 2, 3]")
    with pytest.raises(pf.PortfolioCorrupt):
        pf.get_portfolio()


def test_non_list_holdings_raises(store):
    _write(store, json.dumps({"holdings": {"600000": 1}}))
    with pytest.raises(pf.PortfolioCorrupt):
        pf.get_portfolio()


def test_legacy_ledger_without_holdings_key_is_tolerated(store):
    """老账本没有 holdings 键：补空数组，不当作损坏（避免把正常文件说成坏了）。"""
    _write(store, json.dumps({"last_refresh": None}))
    assert pf.get_portfolio()["holdings"] == []


def test_corrupt_ledger_is_never_overwritten_by_write_path(store):
    """关键：损坏后加仓绝不能把空账本写回去。"""
    _write(store, "{broken")
    before = store.read_bytes()
    with pytest.raises(pf.PortfolioCorrupt):
        pf.add_holding("600000", 100, 10.0)
    assert store.read_bytes() == before, "损坏的账本被覆盖 ⇒ 真实仓位永久丢失"


def test_endpoint_reports_corruption_instead_of_empty_portfolio(store):
    _write(store, "{broken")
    r = client.get("/api/portfolio")
    assert r.status_code == 500, "账本损坏不能回 200 + holdings:[]"
    assert "不代表你没有持仓" in r.json()["detail"]


def test_add_endpoint_reports_corruption_and_leaves_file_intact(store):
    _write(store, "{broken")
    before = store.read_bytes()
    r = client.post("/api/portfolio/holding", json={"code": "600000", "shares": 100, "cost": 10.0})
    assert r.status_code == 500
    assert "不代表你没有持仓" in r.json()["detail"]
    assert store.read_bytes() == before
