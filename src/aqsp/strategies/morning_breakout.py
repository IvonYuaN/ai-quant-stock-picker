from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import (
    MorningBreakoutThresholds,
    Thresholds,
    load_thresholds,
)


@dataclass(frozen=True)
class BreakoutSignal:
    symbol: str
    name: str
    signal_type: str
    score: float
    current_price: float
    target_price: float
    stop_loss: float
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    confidence: float
    entry_time: str
    position_pct: float


class MorningBreakoutStrategy(BaseStrategy):
    name: str = "morning_breakout"

    def __init__(
        self,
        config: StrategyConfig | None = None,
        thresholds: Thresholds | None = None,
    ):
        self.thresholds = thresholds or load_thresholds()
        self.mb: MorningBreakoutThresholds = self.thresholds.morning_breakout
        config = config or StrategyConfig(name="morning_breakout")
        super().__init__(
            config,
            id="morning_breakout",
            version=self.thresholds.version,
            hypothesis="强势股在早盘集合竞价阶段展现高开特征，配合量能放大可捕捉涨停板机会",
            regime_required=("stable_bull", "volatile_bull"),
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        for symbol, df in data.items():
            if df is None or df.empty:
                scores[symbol] = 0.0
                continue
            scores[symbol] = self._calculate_single_score(df)
        return scores

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        df = df.sort_values("date").tail(30)
        if len(df) < 5:
            return 0.0

        change_pct = self._calc_change_pct(df)
        if change_pct < self.mb.min_change_pct:
            return 0.0

        w = self.mb.weights
        score = 0.0
        score += self._score_change_pct(change_pct) * w.change_pct
        score += self._score_volume(df) * w.volume
        score += self._score_technical(df) * w.technical
        score += self._score_fund_flow(df) * w.fund_flow
        score += self._score_market() * w.market

        return max(0.0, min(1.0, score))

    def analyze_pre_market(self, data: Dict[str, pd.DataFrame]) -> List[BreakoutSignal]:
        signals: List[BreakoutSignal] = []

        for symbol, df in data.items():
            if df is None or df.empty or len(df) < 5:
                continue

            df = df.sort_values("date").tail(30)
            change_pct = self._calc_change_pct(df)

            if change_pct < self.mb.min_change_pct:
                continue

            score_raw = self._calculate_single_score(df)
            score_100 = score_raw * 100

            if score_100 < self.mb.min_score:
                continue

            reasons: list[str] = []
            risks: list[str] = []
            self._collect_reasons(df, change_pct, reasons, risks)

            signal_type = self._determine_signal_type(change_pct)
            confidence = self._calc_confidence(score_100, len(reasons), len(risks))
            current_price = float(df["close"].iloc[-1])
            target_price = self._calc_target_price(current_price, change_pct)
            stop_loss = self._calc_stop_loss(current_price, df)
            entry_time = self._determine_entry_time(change_pct, score_100)
            position_pct = self._determine_position(score_100, confidence)

            signals.append(
                BreakoutSignal(
                    symbol=symbol,
                    name=str(df["name"].iloc[-1]) if "name" in df.columns else symbol,
                    signal_type=signal_type,
                    score=round(score_100, 1),
                    current_price=round(current_price, 2),
                    target_price=round(target_price, 2),
                    stop_loss=round(stop_loss, 2),
                    reasons=tuple(reasons),
                    risks=tuple(risks),
                    confidence=round(confidence, 2),
                    entry_time=entry_time,
                    position_pct=round(position_pct, 2),
                )
            )

        signals.sort(key=lambda x: x.score, reverse=True)
        return signals

    def _calc_change_pct(self, df: pd.DataFrame) -> float:
        if len(df) < 2:
            return 0.0
        prev_close = float(df["close"].iloc[-2])
        current = float(df["close"].iloc[-1])
        if prev_close <= 0:
            return 0.0
        return (current - prev_close) / prev_close * 100

    def _score_change_pct(self, change_pct: float) -> float:
        if change_pct >= self.mb.near_limit_pct:
            return self.mb.change_score_near_limit
        if change_pct >= self.mb.strong_pct:
            return self.mb.change_score_strong
        return self.mb.change_score_default

    def _score_volume(self, df: pd.DataFrame) -> float:
        if len(df) < 6:
            return 0.0
        avg_vol = float(df["volume"].iloc[-6:-1].mean())
        current_vol = float(df["volume"].iloc[-1])
        if avg_vol <= 0:
            return 0.0
        ratio = current_vol / avg_vol
        if ratio >= self.mb.volume_ratio_strong:
            return self.mb.volume_score_strong
        if ratio >= self.mb.volume_ratio_medium:
            return self.mb.volume_score_medium
        if ratio < self.mb.volume_ratio_min:
            return 0.0
        return self.mb.volume_score_default

    def _score_technical(self, df: pd.DataFrame) -> float:
        score = 0.0
        if len(df) >= 20:
            ma5 = float(df["close"].rolling(5).mean().iloc[-1])
            ma10 = float(df["close"].rolling(10).mean().iloc[-1])
            ma20 = float(df["close"].rolling(20).mean().iloc[-1])
            if ma5 > ma10 > ma20:
                score += self.mb.technical_ma_bull_score
            recent_high = float(df["high"].iloc[-20:].max())
            if float(df["close"].iloc[-1]) > recent_high:
                score += self.mb.technical_new_high_score
        return min(1.0, score)

    def _score_fund_flow(self, df: pd.DataFrame) -> float:
        if len(df) < 3:
            return 0.0
        vol_3 = float(df["volume"].iloc[-3])
        vol_1 = float(df["volume"].iloc[-1])
        if vol_3 <= 0:
            return 0.0
        ratio = vol_1 / vol_3
        if ratio > self.mb.fund_flow_strong_ratio:
            return self.mb.fund_flow_strong_score
        if ratio > self.mb.fund_flow_medium_ratio:
            return self.mb.fund_flow_medium_score
        return 0.0

    def _score_market(self) -> float:
        return self.mb.market_score

    def _collect_reasons(
        self,
        df: pd.DataFrame,
        change_pct: float,
        reasons: list[str],
        risks: list[str],
    ) -> None:
        if change_pct >= self.mb.near_limit_pct:
            reasons.append("涨幅接近涨停")
        elif change_pct >= self.mb.strong_pct:
            reasons.append(f"涨幅>{self.mb.strong_pct}%")
        else:
            reasons.append(f"涨幅>{self.mb.min_change_pct}%")

        if len(df) >= 6:
            avg_vol = float(df["volume"].iloc[-6:-1].mean())
            current_vol = float(df["volume"].iloc[-1])
            if avg_vol > 0:
                ratio = current_vol / avg_vol
                if ratio >= self.mb.volume_ratio_strong:
                    reasons.append(f"量比{ratio:.1f}倍，资金强势")
                elif ratio >= self.mb.volume_ratio_medium:
                    reasons.append(f"量比{ratio:.1f}倍")
                elif ratio < self.mb.volume_ratio_min:
                    risks.append("量能不足")

        if len(df) >= 20:
            ma5 = float(df["close"].rolling(5).mean().iloc[-1])
            ma10 = float(df["close"].rolling(10).mean().iloc[-1])
            ma20 = float(df["close"].rolling(20).mean().iloc[-1])
            if ma5 > ma10 > ma20:
                reasons.append("均线多头排列")
            recent_high = float(df["high"].iloc[-20:].max())
            if float(df["close"].iloc[-1]) > recent_high:
                reasons.append("突破20日新高")

        if len(df) >= 3:
            vol_3 = float(df["volume"].iloc[-3])
            vol_1 = float(df["volume"].iloc[-1])
            if vol_3 > 0 and vol_1 / vol_3 > self.mb.fund_flow_strong_ratio:
                reasons.append("近期资金持续流入")

    def _determine_signal_type(self, change_pct: float) -> str:
        if change_pct >= self.mb.near_limit_pct:
            return "涨停打板"
        if change_pct >= self.mb.strong_pct:
            return "强势打板"
        return "首板打板"

    def _calc_confidence(
        self, score: float, reasons_count: int, risks_count: int
    ) -> float:
        base = score / 100.0
        bonus = reasons_count * self.mb.confidence_reason_bonus
        penalty = risks_count * self.mb.confidence_risk_penalty
        return max(self.mb.confidence_floor, min(1.0, base + bonus - penalty))

    def _calc_target_price(self, current_price: float, change_pct: float) -> float:
        if change_pct >= self.mb.near_limit_pct:
            return current_price * (1.0 + self.mb.next_day_limit_pct)
        remaining = (self.mb.full_limit_pct - change_pct) / 100.0
        return current_price * (1.0 + remaining)

    def _calc_stop_loss(self, current_price: float, df: pd.DataFrame) -> float:
        if len(df) >= self.mb.atr_period:
            high = df["high"].iloc[-self.mb.atr_period :]
            low = df["low"].iloc[-self.mb.atr_period :]
            close = df["close"].iloc[-self.mb.atr_period :]
            prev_close = close.shift(1)
            tr = pd.concat(
                [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
                axis=1,
            ).max(axis=1)
            atr_val = float(tr.mean())
            return current_price - self.mb.atr_stop_multiplier * atr_val
        return current_price * (1.0 - self.mb.default_stop_pct)

    def _determine_entry_time(self, change_pct: float, score: float) -> str:
        if change_pct >= self.mb.near_limit_pct:
            return "09:25 集合竞价"
        if score >= self.mb.position_high_score:
            return "09:30 开盘瞬间"
        return "09:35 观察后入场"

    def _determine_position(self, score: float, confidence: float) -> float:
        if (
            score >= self.mb.position_high_score
            and confidence >= self.mb.position_high_confidence
        ):
            return self.mb.position_high_pct
        if (
            score >= self.mb.position_mid_score
            and confidence >= self.mb.position_mid_confidence
        ):
            return self.mb.position_mid_pct
        return self.mb.position_low_pct


def format_morning_signals(signals: List[BreakoutSignal], top_n: int = 5) -> str:
    if not signals:
        return "早盘强势股观察：未发现符合条件的股票"

    report: list[str] = []
    report.append("早盘强势股观察")
    report.append("=" * 50)
    report.append(
        f"发现 {len(signals)} 只待复核候选，展示前 {min(top_n, len(signals))} 只:"
    )
    report.append("")

    for i, signal in enumerate(signals[:top_n], 1):
        report.append(f"【{i}】{signal.symbol} {signal.name}")
        report.append(f"   类型: {signal.signal_type}")
        report.append(f"   得分: {signal.score:.1f} 分")
        report.append(f"   现价: {signal.current_price:.2f}")
        report.append(f"   目标: {signal.target_price:.2f}")
        report.append(f"   止损: {signal.stop_loss:.2f}")
        report.append(f"   置信度: {signal.confidence:.0%}")
        report.append(f"   参考仓位: {signal.position_pct:.0%}")
        report.append(f"   观察时点: {signal.entry_time}")
        report.append("   看多理由:")
        for reason in signal.reasons:
            report.append(f"     • {reason}")
        if signal.risks:
            report.append("   ⚠️ 风险提示:")
            for risk in signal.risks:
                report.append(f"     • {risk}")
        report.append("")

    report.append("注意事项:")
    report.append("  1. 早盘急涨波动较高，只先做纸面观察")
    report.append("  2. 严格执行止损纪律")
    report.append("  3. 关注市场整体环境")

    return "\n".join(report)
