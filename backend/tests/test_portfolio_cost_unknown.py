"""成本未知的平仓成交必须记成「未知」，不能记成 0 元成本 / 0% 收益。

历史行为（`_apply_close`）：`cost` 只校验有限性、不校验 `> 0`。补录历史成交
（账本里没有对应持仓）时调用方传 `cost=0`，于是：

- `pnl = (price - 0) * shares` ⇒ 把**整笔卖出额**记成已实现利润；
- `pnl_pct = 0.0` ⇒ 平仓列表显示「收益率 0.00%」，真相是「没有成本依据，算不出来」。

`realized_pnl` 又用 `sum(c.get("pnl", 0) ...)` 把这些伪造值一起加总，量级全错。

本文件的用例锁住四件事：
1. 有成本依据的路径**完全不变**（对照组）；
2. 成本未知 ⇒ `pnl` / `pnl_pct` 为 `None`，`cost` 原样保留；
3. 老账本里已经写坏的 `pnl` / `pnl_pct` 在**读取时**就纠正，不要求数据迁移；
4. `realized_pnl` 只累加有据的成交，并单独披露被排除的笔数。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

import portfolio as pf


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """把持仓目录指到 tmp，并固定「今天」与行情源，避免任何网络。"""
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(pf, "_tenant_id", lambda: "local")
    monkeypatch.setattr(pf, "today_shanghai", lambda: date(2026, 10, 23))
    monkeypatch.setattr(
        pf.astock,
        "tencent_quote",
        lambda codes: {
            code: {"name": code, "price": 9, "quote_time": "20261023100000"}
            for code in codes
        },
    )
    return tmp_path / "portfolio.json"


def test_close_with_holding_uses_ledger_cost_unchanged(ledger) -> None:
    """对照组：账本里有持仓时，成本来自账本，盈亏照常算。"""
    pf.add_holding("600000", 100, 10, "2026-10-22")
    result = pf.close_position("600000", "2026-10-23", 12, 100, 0)
    row = result["closed"][0]
    assert row["cost"] == 10
    assert row["pnl"] == pytest.approx(200)
    assert row["pnl_pct"] == pytest.approx(20)
    assert result["realized_pnl"] == pytest.approx(200)
    assert result["realized_pnl_unknown_count"] == 0


def test_close_with_supplied_cost_basis_is_known(ledger) -> None:
    """补录历史成交时调用方给了真实成本 ⇒ 仍然算得出来。"""
    result = pf.close_position("600000", "2026-10-23", 12, 100, 8)
    row = result["closed"][0]
    assert row["cost"] == 8
    assert row["pnl"] == pytest.approx(400)
    assert row["pnl_pct"] == pytest.approx(50)
    assert result["realized_pnl_unknown_count"] == 0


def test_close_without_cost_basis_records_unknown_not_zero(ledger) -> None:
    """🔴 没有成本依据：盈亏与收益率都是未知，不是 0，更不是整笔成交额。"""
    result = pf.close_position("600000", "2026-10-23", 12, 100, 0)
    row = result["closed"][0]
    assert row["cost"] == 0
    assert row["pnl"] is None, "不能把 price*shares 记成利润"
    assert row["pnl_pct"] is None, "不能把未知写成「没赚没亏」"
    assert result["realized_pnl"] == 0
    assert result["realized_pnl_unknown_count"] == 1


def test_negative_cost_is_treated_as_unknown(ledger) -> None:
    """负成本同样不是依据：不能据此算出「负负得正」的假利润。"""
    result = pf.close_position("600000", "2026-10-23", 12, 100, -5)
    row = result["closed"][0]
    assert row["pnl"] is None
    assert row["pnl_pct"] is None


def test_legacy_zero_cost_row_is_read_as_unknown(ledger) -> None:
    """老账本里已经写坏的记录：读取时就纠正，不需要数据迁移。"""
    ledger.write_text(
        json.dumps(
            {
                "holdings": [],
                "closed": [
                    {
                        "code": "600000",
                        "name": "浦发银行",
                        "date": "2026-10-20",
                        "price": 12,
                        "shares": 100,
                        "cost": 0,
                        "pnl": 1200,
                        "pnl_pct": 0.0,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    data = pf.get_portfolio()
    row = data["closed"][0]
    assert row["pnl"] is None
    assert row["pnl_pct"] is None
    assert data["realized_pnl"] == 0, "伪造的 1200 不能计入已实现盈亏"
    assert data["realized_pnl_unknown_count"] == 1


def test_mixed_legacy_rows_only_sum_known_pnl(ledger) -> None:
    """混合账本：只加有据的，未知笔数单独披露。"""
    ledger.write_text(
        json.dumps(
            {
                "holdings": [],
                "closed": [
                    {
                        "code": "600000",
                        "name": "有据",
                        "date": "2026-10-20",
                        "price": 12,
                        "shares": 100,
                        "cost": 10,
                        "pnl": 200,
                        "pnl_pct": 20.0,
                    },
                    {
                        "code": "600001",
                        "name": "无据",
                        "date": "2026-10-21",
                        "price": 12,
                        "shares": 100,
                        "cost": 0,
                        "pnl": 1200,
                        "pnl_pct": 0.0,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    data = pf.get_portfolio()
    assert data["realized_pnl"] == pytest.approx(200)
    assert data["realized_pnl_unknown_count"] == 1
    assert data["closed"][0]["pnl"] == pytest.approx(200)
    assert data["closed"][1]["pnl"] is None


def test_known_cost_rows_are_not_rewritten_on_read(ledger) -> None:
    """有据的行必须原样返回，别被「纠正」逻辑误伤。"""
    ledger.write_text(
        json.dumps(
            {
                "holdings": [],
                "closed": [
                    {
                        "code": "600000",
                        "name": "有据",
                        "date": "2026-10-20",
                        "price": 9,
                        "shares": 100,
                        "cost": 10,
                        "pnl": -100,
                        "pnl_pct": -10.0,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    data = pf.get_portfolio()
    row = data["closed"][0]
    assert row["pnl"] == pytest.approx(-100)
    assert row["pnl_pct"] == pytest.approx(-10)
    assert data["realized_pnl"] == pytest.approx(-100)
    assert data["realized_pnl_unknown_count"] == 0
