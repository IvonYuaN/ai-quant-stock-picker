from __future__ import annotations

import math

import pandas as pd

from aqsp.indicators import enrich_indicators
from aqsp.internet_strategies import evaluate_strategy_signals
from aqsp.models import PickResult, ScreeningConfig


def screen_universe(
    frames: dict[str, pd.DataFrame], config: ScreeningConfig
) -> list[PickResult]:
    picks: list[PickResult] = []
    for symbol, frame in frames.items():
        try:
            result = score_symbol(symbol, frame, config)
        except (ValueError, IndexError, KeyError, TypeError):
            continue
        if result is not None:
            picks.append(result)
    return sorted(picks, key=lambda item: item.score, reverse=True)


def score_symbol(
    symbol: str, frame: pd.DataFrame, config: ScreeningConfig
) -> PickResult | None:
    df = enrich_indicators(frame)
    if len(df) < config.min_bars:
        return None

    row = df.iloc[-1]
    prev = df.iloc[-2]
    close = float(row["close"])
    if not math.isfinite(close) or close <= 0:
        return None
    if close < config.min_price or close > config.max_price:
        return None

    reasons: list[str] = []
    risks: list[str] = []
    score = 0.0
    strategy_ids: list[str] = []

    avg_amount = _num(row["amount_ma20"])
    if avg_amount < config.min_avg_amount:
        risks.append("20日均成交额不足，流动性过滤")
        score -= 35

    ma5, ma10, ma20, ma60 = (_num(row[f"ma{x}"]) for x in (5, 10, 20, 60))
    bias20 = _num(row["bias20"])
    volume_ratio = _num(row["volume_ratio"])
    ret5 = _num(row["ret_5"]) * 100
    ret20 = _num(row["ret_20"]) * 100
    rsi12 = _num(row["rsi12"])
    atr14 = _num(row["atr14"])
    macd_hist = _num(row["macd_hist"])
    prev_macd_hist = _num(prev["macd_hist"])

    if ma5 > ma10 > ma20 > ma60:
        score += 24
        reasons.append("MA5/10/20/60 多头排列")
    elif ma5 > ma10 > ma20:
        score += 16
        reasons.append("短中期均线多头")
    elif close < ma20:
        score -= 18
        risks.append("收盘价低于 MA20")

    ma20_slope = (ma20 / _num(df.iloc[-6]["ma20"]) - 1) * 100
    if ma20_slope > 1.0:
        score += 10
        reasons.append("MA20 斜率向上")
    elif ma20_slope < -1.5:
        score -= 10
        risks.append("MA20 仍在下行")

    if ret20 > 12:
        score += 14
        reasons.append("20日相对强势")
    elif ret20 < -8:
        score -= 12
        risks.append("20日弱势")

    if close >= _num(prev["high_20"]) * 0.995 and volume_ratio >= 1.35:
        score += 18
        reasons.append("接近或突破20日新高且量能确认")

    pullback_to_ma = (
        ma5 * 0.985 <= close <= ma10 * 1.025
        and volume_ratio <= 1.1
        and ma5 > ma10 > ma20
    )
    if pullback_to_ma:
        score += 16
        reasons.append("强趋势缩量回踩均线")

    if macd_hist > 0 and macd_hist > prev_macd_hist:
        score += 8
        reasons.append("MACD 动能改善")
    elif macd_hist < 0 and macd_hist < prev_macd_hist:
        score -= 8
        risks.append("MACD 动能走弱")

    if 45 <= rsi12 <= 72:
        score += 7
        reasons.append("RSI 位于健康强势区间")
    elif rsi12 > 82:
        score -= 12
        risks.append("RSI 过热")

    if bias20 > config.max_bias20:
        score -= 18
        risks.append("MA20 乖离过高，追高风险")
    elif 0 <= bias20 <= 8:
        score += 8
        reasons.append("价格相对 MA20 位置不拥挤")

    if config.mode == "close":
        if _num(row["range_pos"]) >= 0.68:
            score += 6
            reasons.append("尾盘收在日内偏强区域")
        if _num(row["upper_shadow_pct"]) > 4 and volume_ratio > 1.5:
            score -= 14
            risks.append("尾盘放量长上影")
    else:
        if abs(bias20) <= 10 and volume_ratio < 2.5:
            score += 5
            reasons.append("开盘候选未明显过热")
        if _num(row["amplitude_pct"]) > 9:
            score -= 8
            risks.append("前一交易日振幅过大")

    strategy_signals = evaluate_strategy_signals(df)
    for signal in strategy_signals:
        weight = config.strategy_weights.get(signal.strategy_id, 1.0)
        score += signal.score * weight
        strategy_ids.append(signal.strategy_id)
        reasons.append(f"{signal.display_name}: {'/'.join(signal.reasons[:2])}")

    entry_type = _entry_type(row, prev, pullback_to_ma)
    stop_base = min(ma20, close - atr14 * 1.2) if atr14 > 0 else ma20
    stop_loss = max(stop_base * (1 - config.stop_loss_buffer), 0.01)
    take_profit = close + max(close - stop_loss, atr14) * 1.8
    position = _position(score, risks)
    rating = _rating(score)

    return PickResult(
        symbol=str(row.get("symbol") or symbol),
        name=str(row.get("name") or ""),
        date=str(row["date"]),
        close=round(close, 3),
        score=round(score, 2),
        rating=rating,
        entry_type=entry_type,
        ideal_buy=round(close, 3),
        stop_loss=round(stop_loss, 3),
        take_profit=round(take_profit, 3),
        position=position,
        strategies=tuple(strategy_ids),
        reasons=tuple(reasons[:6]),
        risks=tuple(risks[:5]),
        metrics={
            "ret5_pct": round(ret5, 2),
            "ret20_pct": round(ret20, 2),
            "bias20_pct": round(bias20, 2),
            "volume_ratio": round(volume_ratio, 2),
            "rsi12": round(rsi12, 2),
            "avg_amount_20": round(avg_amount, 2),
        },
    )


def _entry_type(row: pd.Series, prev: pd.Series, pullback_to_ma: bool) -> str:
    if pullback_to_ma:
        return "trend_pullback"
    if (
        _num(row["close"]) >= _num(prev["high_20"]) * 0.995
        and _num(row["volume_ratio"]) >= 1.35
    ):
        return "volume_breakout"
    if _num(row["rsi12"]) < 42 and _num(row["macd_hist"]) > _num(prev["macd_hist"]):
        return "reversal_watch"
    return "relative_strength"


def _position(score: float, risks: list[str]) -> str:
    if score >= 68 and len(risks) <= 1:
        return "30%-50%"
    if score >= 52:
        return "10%-30%"
    return "watch"


def _rating(score: float) -> str:
    if score >= 70:
        return "strong_buy_candidate"
    if score >= 55:
        return "buy_candidate"
    if score >= 40:
        return "watch"
    return "avoid"


def _num(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number
