"""龙虎榜详情（`backend/astock.dragon_tiger_board`）的缺数语义。

为什么值得测：东财 datacenter 对部分标的（实测可转债 `123283`）**不给 `TURNOVERRATE` 列**，
返回 `None`。而原写法是 `round(float(v or 0), 2)` —— `None or 0` = 0，于是「未披露」
被渲染成「换手率 0.00%」。上了龙虎榜的票换手率不可能是 0，这是把未知说成一个确定的数。

同样的坑在金额列：`round((v or 0) / 10000, 1)` 会把「净买额未披露」写成「净买 0 万元」。

（同 `#648`/`#650` 的口径：缺数 → `None`，前端已有 `null → 「—」` 的呈现约定。）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import astock  # noqa: E402


def _patch_datacenter(monkeypatch, rows_by_report: dict):
    def fake(report_name: str, **_kwargs):
        return list(rows_by_report.get(report_name, []))

    monkeypatch.setattr(astock, "eastmoney_datacenter", fake)


def test_missing_turnover_is_none_not_zero(monkeypatch):
    """🔴 这条直接对应实测：可转债 123283 的 TURNOVERRATE 是 None。"""
    _patch_datacenter(monkeypatch, {
        "RPT_DAILYBILLBOARD_DETAILSNEW": [
            {"TRADE_DATE": "2026-11-24 00:00:00", "EXPLANATION": "日涨幅偏离值达7%",
             "BILLBOARD_NET_AMT": 4164724.44, "TURNOVERRATE": None},
        ],
    })
    out = astock.dragon_tiger_board("123283")
    rec = out["records"][0]
    assert rec["turnover"] is None, "缺换手率被渲染成 0.00%"
    assert rec["net_buy"] == pytest.approx(416.5, abs=0.1)


def test_missing_net_amount_is_none_not_zero(monkeypatch):
    _patch_datacenter(monkeypatch, {
        "RPT_DAILYBILLBOARD_DETAILSNEW": [
            {"TRADE_DATE": "2026-11-24 00:00:00", "EXPLANATION": "x",
             "BILLBOARD_NET_AMT": None, "TURNOVERRATE": 11.27},
        ],
    })
    rec = astock.dragon_tiger_board("600519")["records"][0]
    assert rec["net_buy"] is None, "未披露的净买额被渲染成「净买 0 万元」"
    assert rec["turnover"] == pytest.approx(11.27)


def test_normal_values_still_rounded(monkeypatch):
    _patch_datacenter(monkeypatch, {
        "RPT_DAILYBILLBOARD_DETAILSNEW": [
            {"TRADE_DATE": "2026-11-24 00:00:00", "EXPLANATION": "x",
             "BILLBOARD_NET_AMT": 119006351.43, "TURNOVERRATE": 11.2717},
        ],
    })
    rec = astock.dragon_tiger_board("301261")["records"][0]
    assert rec["net_buy"] == pytest.approx(11900.6, abs=0.1)
    assert rec["turnover"] == pytest.approx(11.27)


def test_non_finite_and_dash_are_none(monkeypatch):
    """`'-'`（东财停牌占位）、NaN、inf 一律 None —— 都不能变成数。"""
    _patch_datacenter(monkeypatch, {
        "RPT_DAILYBILLBOARD_DETAILSNEW": [
            {"TRADE_DATE": "2026-11-24 00:00:00", "BILLBOARD_NET_AMT": "-",
             "TURNOVERRATE": float("nan")},
            {"TRADE_DATE": "2026-11-21 00:00:00", "BILLBOARD_NET_AMT": float("inf"),
             "TURNOVERRATE": "-"},
        ],
    })
    recs = astock.dragon_tiger_board("600519")["records"]
    for rec in recs:
        assert rec["net_buy"] is None, rec
        assert rec["turnover"] is None, rec


def test_no_records_still_returns_empty_shape(monkeypatch):
    """无上榜记录时结构不变（调用方不该为「没上榜」写第二套分支）。"""
    _patch_datacenter(monkeypatch, {})
    out = astock.dragon_tiger_board("600519")
    assert out["records"] == []
    assert out["seats"] == {"buy": [], "sell": []}
    assert out["institution"]["net_amt"] == 0.0
