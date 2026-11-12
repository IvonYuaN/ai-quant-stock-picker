# -*- coding: utf-8 -*-
"""市场风险快照：趋势 / 广度 / 回撤 —— 只做**风险提示**，不是 alpha 信号。

## 为什么只做风险提示（重要，别被数字骗）
2026-11-12 在 gate 真实逐期收益上验证过（16 臂 × 20 期、L=20）：
把「市场趋势曝光」叠加到策略收益后，**均值 Sharpe 由 +0.66 降到 +0.53（更差）**，
改善仅 3/16 臂；唯一改善的是**最大回撤**（−10.8% → −3.9%）。

⇒ 本模块的输出**只能**用于：
- 提示当前市场环境（趋势向下 / 广度差 / 回撤深）；
- 触发"降低仓位/提高警惕"的**风控动作**。

**不能**用于：宣称能提高收益、给选股加分、作为择时 alpha。

## 三个指标
1. ``trend_20`` / ``trend_60``：全市场等权（中位收益）近 20/60 交易日累计收益。
   中位数抗极值，避免少数暴涨票拉偏。
2. ``breadth``：收盘价站上 20 日均线的股票占比。广度比指数更早反映分化。
3. ``drawdown_252``：等权净值相对近 252 交易日高点的回撤。

## 分级（保守，不追求精细）
- high：trend_20 < 0 **且** breadth < 0.35，或 drawdown_252 ≤ −0.25
- medium：trend_20 < 0 或 breadth < 0.45
- low：其余

阈值写死在这里而不是 yaml：它**不参与打分**，只是提示语分级，
避免污染策略阈值体系（若将来要进打分，必须走 yaml + walk-forward 报告）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

import pandas as pd

__all__ = ["MarketRiskSnapshot", "compute_market_risk"]

_SAMPLE_SYMBOLS = 1200


@dataclass(frozen=True)
class MarketRiskSnapshot:
    """某一交易日的市场风险快照（字段全部为**当日可知**信息）。"""

    as_of: str
    trend_20: float
    trend_60: float
    breadth: float
    drawdown_252: float
    level: Literal["low", "medium", "high"]
    reasons: tuple[str, ...]
    n_symbols: int

    def summary(self) -> str:
        return (
            f"市场风险={self.level}｜20日 {self.trend_20:+.2%}｜60日 {self.trend_60:+.2%}"
            f"｜广度 {self.breadth:.0%}｜回撤 {self.drawdown_252:+.1%}"
        )


def _load_wide(db: str, as_of: str, lookback_days: int = 320) -> pd.DataFrame:
    """读近 ``lookback_days`` 个交易日的收盘价宽表（按票采样，控制内存）。"""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        codes = [
            r[0]
            for r in con.execute(
                "SELECT ts_code FROM daily_qfq WHERE trade_date <= ?"
                " GROUP BY ts_code HAVING COUNT(*) > 200 ORDER BY ts_code LIMIT ?",
                (as_of, _SAMPLE_SYMBOLS),
            )
        ]
        if not codes:
            return pd.DataFrame()
        marks = ",".join("?" * len(codes))
        px = pd.read_sql(
            f"SELECT ts_code, trade_date, close FROM daily_qfq"
            f" WHERE ts_code IN ({marks}) AND trade_date <= ?",
            con,
            params=(*codes, as_of),
        )
    finally:
        con.close()
    wide = px.pivot_table(index="trade_date", columns="ts_code", values="close").sort_index()
    return wide.tail(lookback_days)


def compute_market_risk(
    db: str, as_of: str, *, min_symbols: int = 300
) -> MarketRiskSnapshot | None:
    """计算 ``as_of``（YYYYMMDD）当日的市场风险快照；数据不足返回 ``None``。"""
    wide = _load_wide(db, as_of)
    if wide.empty or wide.shape[0] < 65 or wide.shape[1] < min_symbols:
        return None

    rets = wide.pct_change()
    mkt = rets.median(axis=1).dropna()
    if len(mkt) < 60:
        return None

    trend_20 = float((1 + mkt.tail(20)).prod() - 1)
    trend_60 = float((1 + mkt.tail(60)).prod() - 1)

    ma20 = wide.rolling(20).mean()
    last = wide.iloc[-1]
    above = (last > ma20.iloc[-1]).sum()
    breadth = float(above / max(1, last.notna().sum()))

    eq = (1 + mkt).cumprod()
    peak = eq.cummax()
    drawdown_252 = float((eq.iloc[-1] / peak.iloc[-1]) - 1)

    reasons: list[str] = []
    if trend_20 < 0:
        reasons.append(f"20 日趋势向下（{trend_20:+.1%}）")
    if trend_60 < 0:
        reasons.append(f"60 日趋势向下（{trend_60:+.1%}）")
    if breadth < 0.45:
        reasons.append(f"广度偏弱（仅 {breadth:.0%} 站上 MA20）")
    if drawdown_252 <= -0.25:
        reasons.append(f"距一年高点回撤 {drawdown_252:.0%}")

    if (trend_20 < 0 and breadth < 0.35) or drawdown_252 <= -0.25:
        level: Literal["low", "medium", "high"] = "high"
    elif trend_20 < 0 or breadth < 0.45:
        level = "medium"
    else:
        level = "low"

    return MarketRiskSnapshot(
        as_of=str(mkt.index[-1]),
        trend_20=trend_20,
        trend_60=trend_60,
        breadth=breadth,
        drawdown_252=drawdown_252,
        level=level,
        reasons=tuple(reasons),
        n_symbols=int(wide.shape[1]),
    )
