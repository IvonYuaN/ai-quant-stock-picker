from __future__ import annotations

import math
import logging
from dataclasses import replace

import pandas as pd

from aqsp.core.errors import FreshnessError
from aqsp.candidate_quality import assess_short_term_quality
from aqsp.data.source_readiness import source_role_for_workload
from aqsp.data.validation import DataValidator
from aqsp.data.filters import TradabilityFilter
from aqsp.freshness import assert_live_short_fresh_data
from aqsp.indicators import enrich_indicators
from aqsp.internet_strategies import evaluate_strategy_signals
from aqsp.models import PickResult, ScreeningConfig
from aqsp.regime.strategy_mixer import canonicalize_regime
from aqsp.strategies.thresholds import (
    InternetStrategyThresholds,
    RegimeStrategyWeights,
    ScoringThresholds,
    Thresholds,
    load_thresholds,
)

_logger = logging.getLogger(__name__)

_HISTORICAL_WORKLOADS = frozenset({"walkforward", "pit"})

_INTERNET_STRATEGY_REGIME_BUCKETS: dict[str, str] = {
    "rps_momentum": "momentum",
    "volume_breakout": "volume",
    "ma_pullback": "momentum",
    "bowl_rebound": "mean_reversion",
    "low_vol_trend": "momentum",
    "n_rebound": "triple_rise",
}


def apply_candidate_quality_gate(picks: list[PickResult]) -> list[PickResult]:
    """Keep only quality-approved paper/observation candidates.

    Picks produced by older or test-only paths without a quality status are kept;
    they are not silently reclassified without the deterministic evidence fields.
    """
    result: list[PickResult] = []
    for pick in picks:
        status = str(pick.metrics.get("quality_gate_status", "") or "").strip()
        if not status:
            result.append(pick)
            continue
        if status in {"blocked", "rejected"}:
            _logger.info(
                "质量门剔除 %s: %s",
                pick.symbol,
                "；".join(pick.metrics.get("quality_gate_reasons", ()) or ()),
            )
            continue
        if status != "observe":
            result.append(pick)
            continue
        metrics = dict(pick.metrics)
        metrics.update(
            {
                "paper_review_eligible": False,
                "candidate_status": "质量观察",
                "candidate_blocker": "",
                "quality_gate_action": "observe",
                "candidate_next_step": "等待短线动能与量价确认后，再评估纸面复核",
                "candidate_review_window": "下一次盘中确认",
                "candidate_review_priority": "low",
                "portfolio_action": "observation_only",
            }
        )
        result.append(replace(pick, metrics=metrics))
    return result


def strategy_weights_for_regime(
    thresholds: Thresholds,
    regime: str,
) -> dict[str, float]:
    """Map regime strategy buckets onto concrete screening strategy ids."""
    if not regime:
        return {}
    canonical_regime = canonicalize_regime(regime)
    regime_weights = thresholds.regime.strategy_weights.get(canonical_regime)
    if regime_weights is None:
        legacy_regime = {
            "aggressive_bull": "stable_bull",
            "volatile_bull": "volatile_bull",
            "defensive_bear": "volatile_bear",
            "rotation_sideways": "stable_sideways",
        }.get(canonical_regime)
        if legacy_regime:
            regime_weights = thresholds.regime.strategy_weights.get(legacy_regime)
    if regime_weights is None:
        return {}
    enabled_buckets = _enabled_strategy_buckets(thresholds)
    return {
        strategy_id: _blend_regime_multiplier(thresholds, weight)
        for strategy_id, weight in _internet_strategy_weights(regime_weights).items()
        if _INTERNET_STRATEGY_REGIME_BUCKETS[strategy_id] in enabled_buckets
    }


def _blend_regime_multiplier(thresholds: Thresholds, multiplier: float) -> float:
    composite = thresholds.composite
    return float(composite.base_blend_weight) + float(
        composite.regime_blend_weight
    ) * float(multiplier)


def _enabled_strategy_buckets(thresholds: Thresholds) -> set[str]:
    buckets = {"momentum"}
    composite = thresholds.composite
    if thresholds.volume.enabled and composite.volume_weight > 0:
        buckets.add("volume")
    if thresholds.mean_reversion.enabled and composite.mean_reversion_weight > 0:
        buckets.add("mean_reversion")
    if thresholds.triple_rise.enabled and composite.triple_rise_weight > 0:
        buckets.add("triple_rise")
    return buckets


def _internet_strategy_weights(
    regime_weights: RegimeStrategyWeights,
) -> dict[str, float]:
    return {
        strategy_id: float(getattr(regime_weights, bucket))
        for strategy_id, bucket in _INTERNET_STRATEGY_REGIME_BUCKETS.items()
    }


def screen_universe(
    frames: dict[str, pd.DataFrame],
    config: ScreeningConfig,
    thresholds: Thresholds | None = None,
) -> list[PickResult]:
    """Screen candidate frames into ranked paper-trading picks."""
    current_thresholds = thresholds or load_thresholds()
    scoring = current_thresholds.scoring
    picks: list[PickResult] = []
    validator = DataValidator()
    tradability_filter = TradabilityFilter()
    validated_frames: dict[str, pd.DataFrame] = {}

    names_map: dict[str, str] = {}
    for symbol, frame in frames.items():
        if frame.empty:
            _logger.warning("跳过空数据: %s", symbol)
            continue
        guard_reason = _strategy_frame_guard_reason(symbol, frame)
        if guard_reason:
            _logger.warning("跳过未通过策略数据门禁 %s: %s", symbol, guard_reason)
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
            result = score_symbol(symbol, frame, config, scoring, current_thresholds)
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
    if _strategy_frame_guard_reason(symbol, frame):
        return None
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
    applied_strategy_weights: dict[str, float] = {}
    applied_strategy_weight_reasons: dict[str, str] = {}
    for signal in strategy_signals:
        if (
            config.strategy_weights
            and signal.strategy_id not in config.strategy_weights
        ):
            continue
        weight = config.strategy_weights.get(signal.strategy_id, 1.0)
        applied_strategy_weights[signal.strategy_id] = round(float(weight), 4)
        if signal.strategy_id in config.strategy_weight_reasons:
            applied_strategy_weight_reasons[signal.strategy_id] = (
                config.strategy_weight_reasons[signal.strategy_id]
            )
        score += signal.score * weight
        strategy_ids.append(signal.strategy_id)
        reasons.append(f"{signal.display_name}: {'/'.join(signal.reasons[:2])}")

    entry_type = _entry_type(row, prev, pullback_to_ma, scoring)
    stop_base = (
        min(ma20, close - atr14 * scoring.stop_atr_multiplier) if atr14 > 0 else ma20
    )
    stop_loss = max(stop_base * (1 - config.stop_loss_buffer), 0.01)
    take_profit = close + max(close - stop_loss, atr14) * scoring.take_profit_multiplier
    quality = assess_short_term_quality(
        score=score,
        rating=_rating(score, scoring),
        ret5_pct=ret5,
        ret20_pct=ret20,
        volume_ratio=volume_ratio,
        rsi12=rsi12,
        bias20_pct=bias20,
        ma_trend=ma5 > ma10 > ma20,
        ma_slope_up=ma20_slope > scoring.ma20_slope_up_threshold,
        macd_improving=macd_hist > 0 and macd_hist > prev_macd_hist,
        near_high_confirmed=(
            close >= _num(prev["high_20"]) * scoring.near_high_threshold
            and volume_ratio >= scoring.near_high_volume
        ),
        pullback_confirmed=pullback_to_ma,
        scoring=scoring,
        risk_count=len(risks),
    )
    position = _position(
        score, risks, scoring, max_position_pct=config.max_position_pct
    )
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
            "quality_gate_status": quality.action,
            "quality_gate_reasons": tuple(dict.fromkeys(quality.reasons)),
            "paper_review_eligible": quality.paper_review_eligible,
            "avg_amount_20": round(avg_amount, 2),
            "sector": _text(row.get("sector")),
            "industry": _text(row.get("industry")),
            "data_source": _text(frame.attrs.get("source_name")),
            "data_fetched_at": _text(frame.attrs.get("fetched_at")),
            "data_timestamp_source": _text(frame.attrs.get("timestamp_source")),
            "historical_data_source": _text(frame.attrs.get("historical_source")),
            "strategy_weights": applied_strategy_weights,
            "strategy_weight_reasons": applied_strategy_weight_reasons,
            "technical_evidence": quality.technical_evidence,
            "technical_evidence_count": len(quality.technical_evidence),
            "technical_quality_status": quality.action,
            "quality_gate_action": quality.action,
            **(
                {
                    "candidate_status": "质量观察",
                    "candidate_next_step": "等待短线动量与量能重新确认后，再评估纸面复核",
                    "candidate_review_window": "下一次量价确认时",
                    "candidate_review_priority": "low",
                }
                if quality.action == "observe"
                else {}
            ),
            **(
                {
                    "candidate_status": "质量阻塞",
                    "candidate_blocker": "；".join(quality.reasons),
                    "candidate_next_step": "补足技术证据并重新通过观察门后，再评估",
                    "candidate_review_window": "下一轮信号出现时",
                    "candidate_review_priority": "low",
                }
                if quality.action == "blocked"
                else {}
            ),
        },
        confidence=_compute_confidence(
            len(strategy_ids), score, len(risks), volume_ratio, scoring
        ),
    )


def _strategy_frame_guard_reason(symbol: str, frame: pd.DataFrame) -> str:
    """Reject unprovenance data before it can become a strategy pick."""
    source_name = _text(frame.attrs.get("source_name"))
    if not source_name:
        return "缺少 source_name provenance"

    workload = _text(frame.attrs.get("workload"))
    if workload in _HISTORICAL_WORKLOADS:
        return ""

    source_role = source_role_for_workload(source_name, "live_short")
    if workload and workload != "live_short":
        return f"未知 workload={workload}"
    if source_role != "realtime":
        return f"来源 {source_name} 未验证为实时来源"

    try:
        assert_live_short_fresh_data({symbol: frame})
    except FreshnessError as exc:
        return f"实时数据新鲜度校验失败: {exc}"
    return ""


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


def _position(
    score: float,
    risks: list[str],
    scoring: ScoringThresholds,
    *,
    max_position_pct: float = 1.0,
) -> str:
    if (
        score >= scoring.position_strong_score
        and len(risks) <= scoring.position_strong_risks
    ):
        return _position_range(
            lower_pct=scoring.position_strong_lower_pct,
            upper_pct=scoring.position_strong_upper_pct,
            max_position_pct=max_position_pct,
        )
    if score >= scoring.position_mid_score:
        return _position_range(
            lower_pct=scoring.position_mid_lower_pct,
            upper_pct=scoring.position_mid_upper_pct,
            max_position_pct=max_position_pct,
        )
    return "watch"


def position_for_score(
    score: float,
    risks: list[str],
    scoring: ScoringThresholds,
    *,
    max_position_pct: float = 1.0,
) -> str:
    return _position(
        score,
        risks,
        scoring,
        max_position_pct=max_position_pct,
    )


def _position_range(
    *,
    lower_pct: float,
    upper_pct: float,
    max_position_pct: float,
) -> str:
    capped_upper = max(0.0, min(float(upper_pct), float(max_position_pct)))
    capped_lower = max(0.0, min(float(lower_pct), capped_upper))
    return f"{capped_lower:.0%}-{capped_upper:.0%}"


def _rating(score: float, scoring: ScoringThresholds) -> str:
    if score >= scoring.rating_strong:
        return "strong_buy_candidate"
    if score >= scoring.rating_buy:
        return "buy_candidate"
    if score >= scoring.rating_watch:
        return "watch"
    return "avoid"


def rating_for_score(score: float, scoring: ScoringThresholds) -> str:
    return _rating(score, scoring)


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
