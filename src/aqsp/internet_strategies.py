from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from aqsp.strategies.n_rebound import detect_n_rebound_signal
from aqsp.strategies.thresholds import (
    InternetStrategyThresholds,
    NReboundThresholds,
    Thresholds,
    load_thresholds,
)


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
    "n_rebound": "N字反弹：涨停后缩量回调、结构未破坏、贴近MA5的 A 股短线形态。",
    "low_vol_trend": "低波趋势：趋势不弱且波动收敛，避免纯追高。",
}


def evaluate_strategy_signals(
    df: pd.DataFrame,
    thresholds: InternetStrategyThresholds | Thresholds | None = None,
) -> list[StrategySignal]:
    threshold_snapshot = (
        thresholds if isinstance(thresholds, Thresholds) else load_thresholds()
    )
    cfg = (
        thresholds
        if isinstance(thresholds, InternetStrategyThresholds)
        else threshold_snapshot.internet_strategy
    )
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

    if (
        ret20 >= cfg.rps_ret20_min_pct
        and close >= _num(prev["high_20"]) * cfg.rps_near_high20
        and ma5 > ma10 > ma20
    ):
        signals.append(
            StrategySignal(
                "rps_momentum",
                "RPS相对强度",
                cfg.rps_score,
                ("20日涨幅靠前", "价格接近20日高位", "短中期趋势向上"),
            )
        )

    if (
        close >= _num(prev["high_20"]) * cfg.volume_breakout_near_high20
        and volume_ratio >= cfg.volume_breakout_volume_ratio
        and _num(row["range_pos"]) >= cfg.volume_breakout_range_pos
    ):
        signals.append(
            StrategySignal(
                "volume_breakout",
                "放量突破",
                cfg.volume_breakout_score,
                ("突破20日高点附近", "量能超过5日均量", "收盘位置偏强"),
            )
        )

    if (
        ma5 * cfg.ma_pullback_ma5_lower <= close <= ma10 * cfg.ma_pullback_ma10_upper
        and volume_ratio <= cfg.ma_pullback_volume_max
        and ma5 > ma10 > ma20
    ):
        signals.append(
            StrategySignal(
                "ma_pullback",
                "缩量回踩均线",
                cfg.ma_pullback_score,
                ("多头趋势未破坏", "回踩MA5/MA10附近", "回踩阶段未明显放量"),
            )
        )

    low20 = _num(row["low_20"])
    rebound_from_low = (close / low20 - 1) * 100 if low20 else 0
    if (
        cfg.bowl_rebound_min_pct <= rebound_from_low <= cfg.bowl_rebound_max_pct
        and macd_hist > prev_macd_hist
        and cfg.bowl_rebound_rsi_low <= rsi12 <= cfg.bowl_rebound_rsi_high
    ):
        signals.append(
            StrategySignal(
                "bowl_rebound",
                "碗口反弹",
                cfg.bowl_rebound_score,
                ("从20日低位温和反弹", "MACD动能改善", "RSI尚未过热"),
            )
        )

    if (
        ma20 > ma60
        and cfg.low_vol_bias_min <= bias20 <= cfg.low_vol_bias_max
        and amplitude <= cfg.low_vol_amplitude_max
        and volume_ratio <= cfg.low_vol_volume_max
    ):
        signals.append(
            StrategySignal(
                "low_vol_trend",
                "低波趋势",
                cfg.low_vol_score,
                ("中期趋势向上", "乖离不高", "波动和量能未失控"),
            )
        )

    n_rebound_signal = _evaluate_n_rebound(df, thresholds=threshold_snapshot.n_rebound)
    if n_rebound_signal is not None:
        signals.append(n_rebound_signal)

    return signals


def _evaluate_n_rebound(
    df: pd.DataFrame, *, thresholds: NReboundThresholds
) -> StrategySignal | None:
    signal = detect_n_rebound_signal(df, thresholds=thresholds)
    if signal is None:
        return None
    return StrategySignal(
        "n_rebound",
        "N字反弹",
        signal.score,
        signal.reasons,
    )


def _num(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
