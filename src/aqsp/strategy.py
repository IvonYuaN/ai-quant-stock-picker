from __future__ import annotations

import math
import logging

import pandas as pd

from aqsp.data.validation import DataValidator
from aqsp.data.filters import TradabilityFilter
from aqsp.indicators import enrich_indicators
from aqsp.internet_strategies import evaluate_strategy_signals
from aqsp.models import PickResult, ScreeningConfig
from aqsp.strategies.thresholds import (
    InternetStrategyThresholds,
    RegimeStrategyWeights,
    ScoringThresholds,
    Thresholds,
    load_thresholds,
)

_logger = logging.getLogger(__name__)

_INTERNET_STRATEGY_REGIME_BUCKETS: dict[str, str] = {
    "rps_momentum": "momentum",
    "volume_breakout": "volume",
    "ma_pullback": "momentum",
    "bowl_rebound": "mean_reversion",
    "low_vol_trend": "momentum",
    "n_rebound": "triple_rise",
}


def strategy_weights_for_regime(
    thresholds: Thresholds,
    regime: str,
) -> dict[str, float]:
    """Map regime strategy buckets onto concrete screening strategy ids."""
    if not regime:
        return {}
    regime_weights = thresholds.regime.strategy_weights.get(regime)
    if regime_weights is None:
        return {}
    return _internet_strategy_weights(regime_weights)


def _internet_strategy_weights(
    regime_weights: RegimeStrategyWeights,
) -> dict[str, float]:
    return {
        strategy_id: float(getattr(regime_weights, bucket))
        for strategy_id, bucket in _INTERNET_STRATEGY_REGIME_BUCKETS.items()
    }


def screen_universe(
    frames: dict[str, pd.DataFrame], config: ScreeningConfig
) -> list[PickResult]:
    """Screen candidate frames into ranked paper-trading picks."""
    thresholds = load_thresholds()
    scoring = thresholds.scoring
    picks: list[PickResult] = []
    validator = DataValidator()
    tradability_filter = TradabilityFilter()
    validated_frames: dict[str, pd.DataFrame] = {}

    names_map: dict[str, str] = {}
    for symbol, frame in frames.items():
        if frame.empty:
            _logger.warning("跳过空数据: %s", symbol)
            continue
        validation = validator.validate_ohlc(frame, symbol=symbol)
        nonfatal_errors = [
            error for error in validation.errors if not error.startswith("日涨跌幅超限")
        ]
        if nonfatal_errors:
            _logger.warning("跳过无效数据 %s: %s", symbol, nonfatal_errors)
            continue
        validation_messages = validation.warnings + [
            error for error in validation.errors if error.startswith("日涨跌幅超限")
        ]
        if validation_messages:
            _logger.warning("%s 数据校验警告: %s", symbol, validation_messages)
        validated_frames[symbol] = frame
        names_map[symbol] = str(frame.iloc[-1].get("name", ""))

    symbols = list(validated_frames.keys())

    try:
        filtered_symbols = tradability_filter.filter_all(
            symbols=symbols,
            data=validated_frames,
            names=names_map,
            min_avg_volume=tradability_filter.min_avg_volume_30d,
            min_avg_amount=tradability_filter.min_daily_amount,
        )
        _logger.info(
            "可交易性过滤: %s -> %s (过滤了 %s 只)",
            len(symbols),
            len(filtered_symbols),
            len(symbols) - len(filtered_symbols),
        )
    except Exception as e:
        _logger.warning(f"可交易性过滤器出错: {e}, 继续使用全部符号")
        filtered_symbols = symbols

    for symbol in filtered_symbols:
        frame = validated_frames[symbol]
        try:
            result = score_symbol(symbol, frame, config, scoring, thresholds)
        except (ValueError, IndexError, KeyError, TypeError) as exc:
            _logger.warning("score_symbol %s 异常，已跳过: %s", symbol, exc)
            continue
        if result is not None:
            picks.append(result)
    return sorted(picks, key=lambda item: item.score, reverse=True)


def score_symbol(
    symbol: str,
    frame: pd.DataFrame,
    config: ScreeningConfig,
    scoring: ScoringThresholds,
    internet_strategy: InternetStrategyThresholds | Thresholds | None = None,
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
        score += scoring.liquidity_penalty

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
        score += scoring.ma_full_bull
        reasons.append("MA5/10/20/60 多头排列")
    elif ma5 > ma10 > ma20:
        score += scoring.ma_short_bull
        reasons.append("短中期均线多头")
    elif close < ma20:
        score += scoring.ma_below_ma20
        risks.append("收盘价低于 MA20")

    # ma20_slope_lookback 在 thresholds 里标注为 float，但用作 iloc 负索引必须是 int。
    # 强制 int 转换，避免配置写成 6.0 或用 dataclass 默认值时 iloc[-6.0] 抛 TypeError
    # （被外层 except 静默吞掉会导致整只标的被跳过、选股大面积失效）。
    slope_lookback = int(scoring.ma20_slope_lookback)
    if len(df) > slope_lookback:
        ma20_ref = _num(df.iloc[-slope_lookback]["ma20"])
        ma20_slope = (ma20 / ma20_ref - 1) * 100 if ma20_ref > 0 else 0.0
    else:
        ma20_slope = 0.0
    if ma20_slope > scoring.ma20_slope_up_threshold:
        score += scoring.ma20_slope_up
        reasons.append("MA20 斜率向上")
    elif ma20_slope < scoring.ma20_slope_down_threshold:
        score += scoring.ma20_slope_down
        risks.append("MA20 仍在下行")

    if ret20 > scoring.ret20_strong_threshold:
        score += scoring.ret20_strong
        reasons.append("20日相对强势")
    elif ret20 < scoring.ret20_weak_threshold:
        score += scoring.ret20_weak
        risks.append("20日弱势")

    if (
        close >= _num(prev["high_20"]) * scoring.near_high_threshold
        and volume_ratio >= scoring.near_high_volume
    ):
        score += scoring.near_high_bonus
        reasons.append("接近或突破20日新高且量能确认")

    pullback_to_ma = (
        ma5 * scoring.pullback_ma5_lower <= close <= ma10 * scoring.pullback_ma10_upper
        and volume_ratio <= scoring.pullback_volume_max
        and ma5 > ma10 > ma20
    )
    if pullback_to_ma:
        score += scoring.pullback_bonus
        reasons.append("强趋势缩量回踩均线")

    if macd_hist > 0 and macd_hist > prev_macd_hist:
        score += scoring.macd_improve
        reasons.append("MACD 动能改善")
    elif macd_hist < 0 and macd_hist < prev_macd_hist:
        score += scoring.macd_weaken
        risks.append("MACD 动能走弱")

    if scoring.rsi_healthy_low <= rsi12 <= scoring.rsi_healthy_high:
        score += scoring.rsi_healthy_bonus
        reasons.append("RSI 位于健康强势区间")
    elif rsi12 > scoring.rsi_overbought:
        score += scoring.rsi_overbought_penalty
        risks.append("RSI 过热")

    if bias20 > config.max_bias20:
        score += scoring.bias_high_penalty
        risks.append("MA20 乖离过高，追高风险")
    elif 0 <= bias20 <= scoring.bias_healthy_max:
        score += scoring.bias_healthy_bonus
        reasons.append("价格相对 MA20 位置不拥挤")

    if config.mode == "close":
        if _num(row["range_pos"]) >= scoring.range_strong_threshold:
            score += scoring.range_strong_bonus
            reasons.append("尾盘收在日内偏强区域")
        if (
            _num(row["upper_shadow_pct"]) > scoring.upper_shadow_threshold
            and volume_ratio > scoring.upper_shadow_volume
        ):
            score += scoring.upper_shadow_penalty
            risks.append("尾盘放量长上影")
    else:
        if (
            abs(bias20) <= scoring.open_calm_bias
            and volume_ratio < scoring.open_calm_volume
        ):
            score += scoring.open_calm_bonus
            reasons.append("开盘候选未明显过热")
        if _num(row["amplitude_pct"]) > scoring.amplitude_threshold:
            score += scoring.amplitude_penalty
            risks.append("前一交易日振幅过大")

    strategy_signals = evaluate_strategy_signals(df, thresholds=internet_strategy)
    for signal in strategy_signals:
        weight = config.strategy_weights.get(signal.strategy_id, 1.0)
        score += signal.score * weight
        strategy_ids.append(signal.strategy_id)
        reasons.append(f"{signal.display_name}: {'/'.join(signal.reasons[:2])}")

    entry_type = _entry_type(row, prev, pullback_to_ma, scoring)
    stop_base = (
        min(ma20, close - atr14 * scoring.stop_atr_multiplier) if atr14 > 0 else ma20
    )
    stop_loss = max(stop_base * (1 - config.stop_loss_buffer), 0.01)
    take_profit = close + max(close - stop_loss, atr14) * scoring.take_profit_multiplier
    position = _position(score, risks, scoring)
    rating = _rating(score, scoring)

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
            "sector": _text(row.get("sector")),
            "industry": _text(row.get("industry")),
        },
        confidence=_compute_confidence(
            len(strategy_ids), score, len(risks), volume_ratio, scoring
        ),
    )


def _compute_confidence(
    strategy_count: int,
    score: float,
    risk_count: int,
    volume_ratio: float,
    scoring: ScoringThresholds,
) -> float:
    conf = 0.0
    conf += min(
        strategy_count * scoring.confidence_strategy_weight,
        scoring.confidence_max_strategies,
    )
    if score >= scoring.confidence_high_score:
        conf += scoring.confidence_high_bonus
    elif score >= scoring.confidence_mid_score:
        conf += scoring.confidence_mid_bonus
    elif score >= scoring.confidence_low_score:
        conf += scoring.confidence_low_bonus
    conf += max(
        0, scoring.confidence_risk_base - risk_count * scoring.confidence_risk_penalty
    )
    if scoring.confidence_volume_low <= volume_ratio <= scoring.confidence_volume_high:
        conf += scoring.confidence_volume_bonus
    elif volume_ratio > scoring.confidence_volume_high:
        conf += scoring.confidence_volume_high_bonus
    return round(min(conf, 100), 1)


def _entry_type(
    row: pd.Series, prev: pd.Series, pullback_to_ma: bool, scoring: ScoringThresholds
) -> str:
    if pullback_to_ma:
        return "trend_pullback"
    if (
        _num(row["close"]) >= _num(prev["high_20"]) * scoring.near_high_threshold
        and _num(row["volume_ratio"]) >= scoring.near_high_volume
    ):
        return "volume_breakout"
    if _num(row["rsi12"]) < scoring.reversal_rsi_threshold and _num(
        row["macd_hist"]
    ) > _num(prev["macd_hist"]):
        return "reversal_watch"
    return "relative_strength"


def _position(score: float, risks: list[str], scoring: ScoringThresholds) -> str:
    if (
        score >= scoring.position_strong_score
        and len(risks) <= scoring.position_strong_risks
    ):
        return "30%-50%"
    if score >= scoring.position_mid_score:
        return "10%-30%"
    return "watch"


def _rating(score: float, scoring: ScoringThresholds) -> str:
    if score >= scoring.rating_strong:
        return "strong_buy_candidate"
    if score >= scoring.rating_buy:
        return "buy_candidate"
    if score >= scoring.rating_watch:
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


def _text(value: object) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return ""
    return text
