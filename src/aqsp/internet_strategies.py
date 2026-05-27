from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class StrategySignal:
    strategy_id: str
    display_name: str
    score: float
    reasons: tuple[str, ...]


STRATEGY_SOURCES = {
    "rps_momentum": "RPS/相对强度动量：借鉴 InStock 综合指标与选股验证思路、A-share selector 相对强弱筛选。",
    "volume_breakout": "放量突破：常见 A 股短线策略，借鉴开源选股项目的放量阳线/突破形态。",
    "ma_pullback": "均线缩量回踩：趋势延续低吸策略，来自通达信/技术分析开源策略常见模板。",
    "bowl_rebound": "碗口反弹：借鉴 A-share Quant Selector 的 BowlReboundStrategy 思路。",
    "low_vol_trend": "低波趋势：趋势不弱且波动收敛，避免纯追高。",
}


def evaluate_strategy_signals(df: pd.DataFrame) -> list[StrategySignal]:
    row = df.iloc[-1]
    prev = df.iloc[-2]
    signals: list[StrategySignal] = []

    close = _num(row["close"])
    ma5 = _num(row["ma5"])
    ma10 = _num(row["ma10"])
    ma20 = _num(row["ma20"])
    ma60 = _num(row["ma60"])
    volume_ratio = _num(row["volume_ratio"])
    ret20 = _num(row["ret_20"]) * 100
    bias20 = _num(row["bias20"])
    rsi12 = _num(row["rsi12"])
    macd_hist = _num(row["macd_hist"])
    prev_macd_hist = _num(prev["macd_hist"])
    amplitude = _num(row["amplitude_pct"])

    if ret20 >= 10 and close >= _num(prev["high_20"]) * 0.98 and ma5 > ma10 > ma20:
        signals.append(
            StrategySignal(
                "rps_momentum",
                "RPS相对强度",
                14,
                ("20日涨幅靠前", "价格接近20日高位", "短中期趋势向上"),
            )
        )

    if close >= _num(prev["high_20"]) * 0.995 and volume_ratio >= 1.35 and _num(row["range_pos"]) >= 0.62:
        signals.append(
            StrategySignal(
                "volume_breakout",
                "放量突破",
                18,
                ("突破20日高点附近", "量能超过5日均量", "收盘位置偏强"),
            )
        )

    if ma5 * 0.985 <= close <= ma10 * 1.025 and volume_ratio <= 1.1 and ma5 > ma10 > ma20:
        signals.append(
            StrategySignal(
                "ma_pullback",
                "缩量回踩均线",
                16,
                ("多头趋势未破坏", "回踩MA5/MA10附近", "回踩阶段未明显放量"),
            )
        )

    low20 = _num(row["low_20"])
    rebound_from_low = (close / low20 - 1) * 100 if low20 else 0
    if 4 <= rebound_from_low <= 18 and macd_hist > prev_macd_hist and 35 <= rsi12 <= 58:
        signals.append(
            StrategySignal(
                "bowl_rebound",
                "碗口反弹",
                12,
                ("从20日低位温和反弹", "MACD动能改善", "RSI尚未过热"),
            )
        )

    if ma20 > ma60 and 0 <= bias20 <= 8 and amplitude <= 5.5 and volume_ratio <= 1.8:
        signals.append(
            StrategySignal(
                "low_vol_trend",
                "低波趋势",
                10,
                ("中期趋势向上", "乖离不高", "波动和量能未失控"),
            )
        )

    return signals


def _num(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
