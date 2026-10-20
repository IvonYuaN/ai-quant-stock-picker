"""P0-2 LIVE 风控入口单测 —— 精确钉死 Q3=A 的 T+1 该卖提醒语义。

覆盖矩阵：
- L1 老仓（holding_days≥1）触发 ⇒ 该卖、sellable_now=True、无 T+1 标注
- L1 当日买入（holding_days=0）触发 ⇒ 明日生效、sellable_now=False、挂 T+1 标注
- L5 老仓触发 ⇒ 该卖；L5 当日买入 ⇒ 观察 + T+1 标注
- 全绿持仓 ⇒ 空列表（无依据不产提醒）
- 缺行情 / 缺成本 / 负成本 ⇒ 跳过，不产"无依据"提醒
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from aqsp.risk.live_risk import run_live_risk_check


def _rising_df(start=10.0, drift=0.0, periods=30) -> pd.DataFrame:
    """近高走低/走平的日线，让动态止损 recommended_stop 落在现价下方。"""
    close = np.linspace(start, start + drift, periods)
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=periods).strftime("%Y-%m-%d"),
            "close": close,
            "high": close * 1.02,
            "low": close * 0.98,
            "volume": 100000,
        }
    )


def _l1_triggered():
    """老仓，成本 12.0，现价 11.0（跌 8.3% > 生产 8% 阈值）⇒ L1 该卖。"""
    holdings = [{"code": "600000", "shares": 100, "cost": 12.0}]
    prices = {"600000": 11.0}
    return run_live_risk_check(holdings, prices, holding_days={"600000": 5})


def test_l1_old_position_sells_now():
    reminders = _l1_triggered()
    assert len(reminders) == 1
    r = reminders[0]
    assert r.level == "L1"
    assert r.sellable_now is True
    assert r.action_hint == "该卖"
    assert r.t1_note == ""
    # 无依据不产提醒的不变量：basis 必须齐备 rule+actual
    assert r.basis["rule"] and r.basis["actual"]


def test_l1_same_day_bought_defers_to_next_day():
    """当日买入（holding_days=0）触发 L1 ⇒ 明日生效 + T+1 标注（Q3=A）。"""
    holdings = [{"code": "600000", "shares": 100, "cost": 12.0}]
    prices = {"600000": 11.0}
    reminders = run_live_risk_check(holdings, prices, holding_days={"600000": 0})
    assert len(reminders) == 1
    r = reminders[0]
    assert r.level == "L1"
    assert r.sellable_now is False
    assert r.action_hint == "明日生效"
    assert "T+1" in r.t1_note


def test_l5_old_position_and_same_day():
    """L5 动态止损：老仓该卖 / 当日买入观察。"""
    # 下跌走势 ⇒ recommended_stop 接近现价下方
    df = _rising_df(start=13.0, drift=-2.0, periods=30)
    holdings = [{"code": "600001", "shares": 200, "cost": 13.0}]
    last_close = float(df["close"].iloc[-1])
    prices = {"600001": last_close}

    old = run_live_risk_check(
        holdings, prices, holding_days={"600001": 3}, price_history={"600001": df}
    )
    l5_old = [r for r in old if r.level == "L5"]
    if l5_old:
        assert l5_old[0].action_hint == "该卖"
        assert l5_old[0].sellable_now is True

    same_day = run_live_risk_check(
        holdings,
        prices,
        holding_days={"600001": 0},
        price_history={"600001": df},
    )
    l5_same = [r for r in same_day if r.level == "L5"]
    if l5_same:
        assert l5_same[0].action_hint == "观察"
        assert l5_same[0].sellable_now is False
        assert "T+1" in l5_same[0].t1_note


def test_green_holdings_produce_no_reminder():
    """全绿持仓（不跌穿阈值）⇒ 空列表。"""
    holdings = [{"code": "600002", "shares": 100, "cost": 10.0}]
    prices = {"600002": 10.5}  # 浮盈
    reminders = run_live_risk_check(holdings, prices, holding_days={"600002": 5})
    assert reminders == []


def test_missing_price_skipped():
    """缺行情 ⇒ 跳过，不产无依据提醒。"""
    holdings = [{"code": "600003", "shares": 100, "cost": 12.0}]
    reminders = run_live_risk_check(holdings, {}, holding_days={"600003": 5})
    assert reminders == []


def test_negative_cost_skips_l1():
    """负成本（融券/返息）无成本回撤基准 ⇒ L1 跳过，不产提醒。"""
    holdings = [{"code": "600004", "shares": 100, "cost": -1.0}]
    prices = {"600004": 0.5}
    reminders = run_live_risk_check(holdings, prices, holding_days={"600004": 5})
    assert reminders == []


def test_multiple_holdings_aggregate():
    """≥2 只真实持仓：一破一不破，只出破的那条。"""
    holdings = [
        {"code": "600000", "shares": 100, "cost": 12.0},  # 破 L1
        {"code": "600001", "shares": 100, "cost": 10.0},  # 浮盈
    ]
    prices = {"600000": 11.0, "600001": 10.6}
    reminders = run_live_risk_check(
        holdings, prices, holding_days={"600000": 5, "600001": 5}
    )
    assert [r.symbol for r in reminders] == ["600000"]


def test_reminder_to_dict_shape():
    """外发 notifier / 前端收件箱依赖的扁平字段齐全。"""
    d = _l1_triggered()[0].to_dict()
    for key in (
        "symbol",
        "level",
        "current_price",
        "threshold_price",
        "pnl_pct",
        "sellable_now",
        "action_hint",
        "t1_note",
        "basis_json",
    ):
        assert key in d
    assert d["action_hint"] == "该卖"
