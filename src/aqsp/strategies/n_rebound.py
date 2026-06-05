from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import NReboundThresholds, Thresholds, load_thresholds


@dataclass(frozen=True)
class NReboundSignal:
    pullback_pct: float
    volume_ratio_to_limit_up: float
    ma5_deviation_pct: float
    days_since_limit_up: int
    score: float
    reasons: tuple[str, ...]


def detect_n_rebound_signal(
    df: pd.DataFrame,
    *,
    thresholds: NReboundThresholds | None = None,
) -> NReboundSignal | None:
    cfg = thresholds or load_thresholds().n_rebound
    if not cfg.enabled:
        return None

    lookback = max(int(cfg.lookback_days), 20)
    if df is None or df.empty or len(df) < lookback + 5:
        return None

    required_cols = {"close", "open", "volume", "ma5", "ma20"}
    if not required_cols.issubset(df.columns):
        return None

    recent = df.sort_values("date").tail(lookback + 1).reset_index(drop=True).copy()
    recent["daily_ret_pct"] = recent["close"].pct_change() * 100
    current = recent.iloc[-1]
    candidates = recent.index[
        (recent["daily_ret_pct"] >= cfg.limit_up_min_pct)
    ].to_numpy()
    candidates = candidates[candidates < len(recent) - 1]
    if len(candidates) == 0:
        return None

    limit_idx = int(candidates[-1])
    days_since = len(recent) - 1 - limit_idx
    if days_since <= 0 or days_since > cfg.max_days_since_limit_up:
        return None

    limit_row = recent.iloc[limit_idx]
    after_limit = recent.iloc[limit_idx + 1 :]
    if after_limit.empty:
        return None

    limit_close = _num(limit_row["close"])
    current_close = _num(current["close"])
    limit_open = _num(limit_row["open"])
    current_ma5 = _num(current["ma5"])
    if min(limit_close, current_close, current_ma5) <= 0:
        return None

    pullback_pct = (limit_close - current_close) / limit_close * 100
    if pullback_pct < cfg.pullback_min_pct or pullback_pct > cfg.pullback_max_pct:
        return None
    if current_close >= limit_close:
        return None

    current_volume = _num(current["volume"])
    limit_volume = _num(limit_row["volume"])
    volume_ratio = current_volume / limit_volume if limit_volume > 0 else 99.0
    if volume_ratio > cfg.volume_shrink_ratio:
        return None

    ma5_dev_pct = abs(current_close / current_ma5 - 1) * 100
    if ma5_dev_pct > cfg.ma5_deviation_max_pct:
        return None

    structure_floor = limit_open * 0.97 if limit_open > 0 else 0.0
    if structure_floor > 0 and float(after_limit["close"].min()) < structure_floor:
        return None

    score = 0.0
    reasons: list[str] = []

    score += 6.0 if 5.0 <= pullback_pct <= 10.0 else 4.0
    reasons.append(f"涨停后回调 {pullback_pct:.1f}%")

    score += 4.0 if volume_ratio <= 0.60 else 2.5
    reasons.append(f"量能缩至涨停日的 {volume_ratio:.0%}")

    score += 4.0 if ma5_dev_pct <= 3.0 else 2.0
    reasons.append(f"现价距MA5仅 {ma5_dev_pct:.1f}%")

    score += 3.0 if days_since <= 5 else 1.5
    reasons.append(f"距涨停仅 {days_since} 天")

    current_ma20 = _num(current["ma20"])
    if current_ma20 > 0 and current_close >= current_ma20:
        score += 2.0
        reasons.append("回调后仍站在 MA20 上方")

    if score < cfg.min_score:
        return None

    return NReboundSignal(
        pullback_pct=round(pullback_pct, 2),
        volume_ratio_to_limit_up=round(volume_ratio, 4),
        ma5_deviation_pct=round(ma5_dev_pct, 2),
        days_since_limit_up=days_since,
        score=round(score, 2),
        reasons=tuple(reasons[:4]),
    )


class NReboundStrategy(BaseStrategy):
    name: str = "n_rebound"

    def __init__(
        self,
        config: StrategyConfig | None = None,
        thresholds: Thresholds | None = None,
    ):
        self.thresholds = thresholds or load_thresholds()
        config = config or StrategyConfig(name="n_rebound")
        super().__init__(
            config,
            id="n_rebound",
            version=self.thresholds.version,
            hypothesis="A股涨停后的缩量回调若结构未破坏，往往意味着强势资金未离场，二次启动概率更高。",
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        for symbol, df in data.items():
            signal = detect_n_rebound_signal(df, thresholds=self.thresholds.n_rebound)
            if signal is None:
                scores[symbol] = 0.0
                continue
            scores[symbol] = min(1.0, max(0.0, signal.score / 20.0))
        return scores

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        signal = detect_n_rebound_signal(df, thresholds=self.thresholds.n_rebound)
        if signal is None:
            return 0.0
        return min(1.0, max(0.0, signal.score / 20.0))


def _num(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
