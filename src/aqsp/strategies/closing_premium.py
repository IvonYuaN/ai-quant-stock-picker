from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds


@dataclass(frozen=True)
class PremiumSignal:
    """溢价信号"""

    symbol: str
    name: str
    signal_type: str
    score: float
    current_price: float
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    confidence: float
    holding_days: int
    expected_return: float


@dataclass(frozen=True)
class ScoreComponents:
    """评分组件"""

    change_pct_score: float = 0.0
    volume_price_score: float = 0.0
    closing_trend_score: float = 0.0
    technical_score: float = 0.0
    support_resistance_score: float = 0.0
    reasons: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()


class ClosingPremiumStrategy(BaseStrategy):
    """尾盘溢价策略

    核心逻辑：
    1. 尾盘阶段（14:30-15:00）筛选异动股票
    2. 分析量价配合、资金流向、技术形态
    3. 筛选次日有溢价潜力的股票
    4. 生成溢价信号
    """

    name: str = "closing_premium"

    def __init__(self, config: StrategyConfig | None = None, thresholds: Thresholds | None = None):
        self.thresholds = thresholds or load_thresholds()
        self.cfg = self.thresholds.closing_premium
        config = config or StrategyConfig(name="closing_premium")
        super().__init__(
            config,
            id="closing_premium",
            version=self.thresholds.version,
            hypothesis="尾盘异动股票往往有资金介入，次日有溢价空间",
            regime_required=("stable_bull", "stable_sideways", "volatile_bull"),
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        scores = {}
        for symbol, df in data.items():
            if df is None or df.empty:
                scores[symbol] = 0.0
                continue
            scores[symbol] = self._calculate_single_score(df)
        return scores

    def _calculate_single_score(self, df: pd.DataFrame) -> float:
        if len(df) < self.cfg.min_data_points:
            return 0.0

        df = df.sort_values("date").tail(self.cfg.lookback_days)
        latest = df.iloc[-1]
        prev_close = df["close"].iloc[-2] if len(df) > 1 else latest["close"]

        change_pct = (latest["close"] - prev_close) / prev_close * 100

        if change_pct < self.cfg.min_change_pct or change_pct > self.cfg.max_change_pct:
            return 0.0

        components = self._calculate_score_components(df, change_pct)
        total_score = self._compute_weighted_score(components)

        return max(0.0, min(1.0, total_score / 100.0))

    def analyze_closing(self, data: Dict[str, pd.DataFrame]) -> List[PremiumSignal]:
        """分析尾盘数据

        Args:
            data: 股票数据字典，key为股票代码，value为DataFrame

        Returns:
            溢价信号列表
        """
        signals = []

        for symbol, df in data.items():
            if df is None or df.empty or len(df) < self.cfg.min_data_points:
                continue

            df = df.sort_values("date").tail(self.cfg.lookback_days)
            latest = df.iloc[-1]
            prev_close = df["close"].iloc[-2] if len(df) > 1 else latest["close"]

            change_pct = (latest["close"] - prev_close) / prev_close * 100

            if (
                change_pct < self.cfg.min_change_pct
                or change_pct > self.cfg.max_change_pct
            ):
                continue

            components = self._calculate_score_components(df, change_pct)
            total_score = self._compute_weighted_score(components)

            if total_score >= self.cfg.min_score:
                entry_price = self._calculate_entry_price(latest["close"], df)
                stop_loss = self._calculate_stop_loss(entry_price, df)
                take_profit_1, take_profit_2 = self._calculate_take_profit(
                    entry_price, df
                )
                signal_type = self._determine_signal_type(df, change_pct)
                confidence = self._calculate_confidence(
                    total_score, len(components.reasons), len(components.risks)
                )
                expected_return = (take_profit_1 - entry_price) / entry_price * 100
                holding_days = self._determine_holding_days(total_score, signal_type)

                signal = PremiumSignal(
                    symbol=symbol,
                    name=latest.get("name", symbol),
                    signal_type=signal_type,
                    score=total_score,
                    current_price=latest["close"],
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit_1=take_profit_1,
                    take_profit_2=take_profit_2,
                    reasons=components.reasons,
                    risks=components.risks,
                    confidence=confidence,
                    holding_days=holding_days,
                    expected_return=expected_return,
                )
                signals.append(signal)

        signals.sort(key=lambda x: x.score, reverse=True)
        return signals

    def _calculate_score_components(
        self, df: pd.DataFrame, change_pct: float
    ) -> ScoreComponents:
        reasons = []
        risks = []

        change_score, change_reasons = self._score_change_pct(change_pct)
        reasons.extend(change_reasons)

        vol_score, vol_reasons = self._analyze_volume_price(df)
        reasons.extend(vol_reasons)

        closing_score, closing_reasons = self._analyze_closing_trend(df)
        reasons.extend(closing_reasons)

        tech_score, tech_reasons = self._analyze_technical_pattern(df)
        reasons.extend(tech_reasons)

        support_score, support_reasons = self._analyze_support_resistance(df)
        reasons.extend(support_reasons)

        if self._check_high_open_risk(df):
            risks.append("高开风险较大")
        if self._check_volume_shrink(df):
            risks.append("量能萎缩，动力不足")

        return ScoreComponents(
            change_pct_score=change_score,
            volume_price_score=vol_score,
            closing_trend_score=closing_score,
            technical_score=tech_score,
            support_resistance_score=support_score,
            reasons=tuple(reasons),
            risks=tuple(risks),
        )

    def _compute_weighted_score(self, components: ScoreComponents) -> float:
        w = self.cfg.weights
        score = (
            components.change_pct_score * w.change_pct
            + components.volume_price_score * w.volume_price
            + components.closing_trend_score * w.closing_trend
            + components.technical_score * w.technical
            + components.support_resistance_score * w.support_resistance
        )
        return min(score, 100.0)

    def _score_change_pct(self, change_pct: float) -> Tuple[float, List[str]]:
        reasons = []

        if self.cfg.optimal_change_min <= change_pct <= self.cfg.optimal_change_max:
            reasons.append("涨幅适中，有上涨空间")
            return 20.0, reasons
        elif self.cfg.min_change_pct <= change_pct < self.cfg.optimal_change_min:
            reasons.append("小幅上涨，可关注")
            return 10.0, reasons
        elif self.cfg.optimal_change_max < change_pct <= self.cfg.max_change_pct:
            reasons.append("涨幅较大，注意追高风险")
            return 15.0, reasons

        return 0.0, reasons

    def _analyze_volume_price(self, df: pd.DataFrame) -> Tuple[float, List[str]]:
        score = 0.0
        reasons = []

        if len(df) < 6:
            return score, reasons

        avg_volume = df["volume"].iloc[-6:-1].mean()
        current_volume = df["volume"].iloc[-1]
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

        price_change = (
            (df["close"].iloc[-1] - df["close"].iloc[-2]) / df["close"].iloc[-2]
            if len(df) >= 2
            else 0.0
        )

        if volume_ratio > self.cfg.volume_ratio_strong and price_change > 0:
            score += 15.0
            reasons.append(f"量价齐升，量比{volume_ratio:.1f}")
        elif volume_ratio > self.cfg.volume_ratio_moderate and price_change > 0:
            score += 10.0
            reasons.append(f"温和放量，量比{volume_ratio:.1f}")

        if len(df) >= 5:
            last_2_vol = df["volume"].iloc[-2:].mean()
            prev_vol = df["volume"].iloc[-5:-2].mean()
            if prev_vol > 0 and last_2_vol > prev_vol * self.cfg.closing_volume_ratio:
                score += 10.0
                reasons.append("尾盘资金流入")

        return score, reasons

    def _analyze_closing_trend(self, df: pd.DataFrame) -> Tuple[float, List[str]]:
        score = 0.0
        reasons = []

        if len(df) < 1:
            return score, reasons

        last_close = df["close"].iloc[-1]
        last_open = df["open"].iloc[-1]

        if last_open > 0:
            closing_change = (last_close - last_open) / last_open * 100

            if closing_change > self.cfg.closing_change_threshold:
                score += 15.0
                reasons.append("尾盘拉升")
            elif closing_change > 0:
                score += 10.0
                reasons.append("尾盘走强")

        if last_close >= df["high"].iloc[-1] * 0.98:
            score += 5.0
            reasons.append("收盘价接近最高价")

        return score, reasons

    def _analyze_technical_pattern(self, df: pd.DataFrame) -> Tuple[float, List[str]]:
        score = 0.0
        reasons = []

        if len(df) < max(self.cfg.ma_periods):
            return score, reasons

        current = df["close"].iloc[-1]
        ma_values = []
        for period in self.cfg.ma_periods:
            ma_val = df["close"].rolling(period).mean().iloc[-1]
            ma_values.append(ma_val)

        if all(current > ma for ma in ma_values) and all(
            ma_values[i] > ma_values[i + 1] for i in range(len(ma_values) - 1)
        ):
            score += 15.0
            reasons.append("均线多头排列")
        elif len(ma_values) >= 2 and current > ma_values[0] > ma_values[1]:
            score += 10.0
            reasons.append("短期均线向上")

        if len(df) >= 26:
            macd, signal, hist = self._calculate_macd(df)
            if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0:
                score += 5.0
                reasons.append("MACD金叉")

        return score, reasons

    def _calculate_macd(
        self, df: pd.DataFrame
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        close = df["close"]
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist = macd - signal
        return macd, signal, hist

    def _analyze_support_resistance(self, df: pd.DataFrame) -> Tuple[float, List[str]]:
        score = 0.0
        reasons = []

        if len(df) < 20:
            return score, reasons

        current = df["close"].iloc[-1]
        recent_low = df["low"].iloc[-20:].min()

        if current > 0:
            support_distance = (current - recent_low) / current * 100
            if support_distance < self.cfg.support_threshold:
                score += 10.0
                reasons.append("接近支撑位，下跌空间有限")

        recent_high = df["high"].iloc[-20:].max()
        if current > 0:
            resistance_distance = (recent_high - current) / current * 100
            if resistance_distance > self.cfg.resistance_threshold:
                score += 5.0
                reasons.append("上方压力较小")

        return score, reasons

    def _check_high_open_risk(self, df: pd.DataFrame) -> bool:
        if len(df) < self.cfg.high_open_check_days:
            return False

        recent_data = df.tail(self.cfg.high_open_check_days)
        high_open_count = sum(
            1 for o, c in zip(recent_data["open"], recent_data["close"]) if o > c
        )

        return high_open_count >= self.cfg.high_open_count_threshold

    def _check_volume_shrink(self, df: pd.DataFrame) -> bool:
        if len(df) < self.cfg.volume_shrink_days + 7:
            return False

        recent_vol = df["volume"].iloc[-self.cfg.volume_shrink_days :].mean()
        avg_vol = (
            df["volume"]
            .iloc[-(self.cfg.volume_shrink_days + 7) : -self.cfg.volume_shrink_days]
            .mean()
        )

        return avg_vol > 0 and recent_vol < avg_vol * self.cfg.volume_shrink_ratio

    def _calculate_entry_price(self, current_price: float, df: pd.DataFrame) -> float:
        if len(df) >= 5:
            ma5 = df["close"].rolling(5).mean().iloc[-1]
            return min(current_price, ma5 * 1.01)
        return current_price

    def _calculate_stop_loss(self, entry_price: float, df: pd.DataFrame) -> float:
        if len(df) >= self.cfg.atr_period:
            atr = self._calculate_atr(df, self.cfg.atr_period)
            return entry_price - self.cfg.atr_stop_multiplier * atr
        return entry_price * (1 - self.cfg.default_stop_pct)

    def _calculate_atr(self, df: pd.DataFrame, period: int) -> float:
        high = df["high"].iloc[-period:]
        low = df["low"].iloc[-period:]
        close = df["close"].iloc[-period:]

        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.mean()

    def _calculate_take_profit(
        self, entry_price: float, df: pd.DataFrame
    ) -> Tuple[float, float]:
        atr = (
            self._calculate_atr(df, self.cfg.atr_period)
            if len(df) >= self.cfg.atr_period
            else entry_price * self.cfg.default_stop_pct
        )

        take_profit_1 = entry_price + self.cfg.atr_tp1_multiplier * atr
        take_profit_2 = entry_price + self.cfg.atr_tp2_multiplier * atr

        return take_profit_1, take_profit_2

    def _determine_signal_type(self, df: pd.DataFrame, change_pct: float) -> str:
        if len(df) < 6:
            return "尾盘拉升"

        avg_volume = df["volume"].iloc[-6:-1].mean()
        if avg_volume > 0:
            volume_ratio = df["volume"].iloc[-1] / avg_volume
            if volume_ratio > 2.0 and change_pct > 3.0:
                return "量价突破"

        if len(df) >= 20:
            ma20 = df["close"].rolling(20).mean().iloc[-1]
            current = df["close"].iloc[-1]
            if ma20 > 0 and ma20 * 0.98 < current < ma20 * 1.02:
                return "均线支撑"

        if len(df) >= 10:
            recent_low = df["low"].iloc[-10:].min()
            if (
                recent_low > 0
                and (df["close"].iloc[-1] - recent_low) / recent_low > 0.05
            ):
                return "底部反转"

        return "尾盘拉升"

    def _calculate_confidence(
        self, score: float, reasons_count: int, risks_count: int
    ) -> float:
        base_confidence = score / 100.0
        reason_bonus = reasons_count * 0.03
        risk_penalty = risks_count * 0.08
        return max(0.1, min(1.0, base_confidence + reason_bonus - risk_penalty))

    def _determine_holding_days(self, score: float, signal_type: str) -> int:
        if signal_type == "量价突破":
            return 3
        elif signal_type == "底部反转":
            return 5
        else:
            return 2


def format_closing_signals(signals: List[PremiumSignal], top_n: int = 5) -> str:
    """格式化尾盘信号为报告"""
    if not signals:
        return "📊 尾盘溢价策略：未发现符合条件的股票"

    report = []
    report.append("📈 尾盘溢价策略推荐")
    report.append("=" * 50)
    report.append(f"发现 {len(signals)} 只潜力股，推荐 Top {min(top_n, len(signals))}:")
    report.append("")

    for i, signal in enumerate(signals[:top_n], 1):
        report.append(f"【{i}】{signal.symbol} {signal.name}")
        report.append(f"   类型: {signal.signal_type}")
        report.append(f"   得分: {signal.score:.1f} 分")
        report.append(f"   现价: {signal.current_price:.2f}")
        report.append(f"   建议入场: {signal.entry_price:.2f}")
        report.append(f"   止损: {signal.stop_loss:.2f}")
        report.append(f"   目标1: {signal.take_profit_1:.2f}")
        report.append(f"   目标2: {signal.take_profit_2:.2f}")
        report.append(f"   置信度: {signal.confidence:.0%}")
        report.append(f"   预期收益: {signal.expected_return:.1f}%")
        report.append(f"   建议持有: {signal.holding_days} 天")
        report.append("   看多理由:")
        for reason in signal.reasons:
            report.append(f"     • {reason}")
        if signal.risks:
            report.append("   ⚠️ 风险提示:")
            for risk in signal.risks:
                report.append(f"     • {risk}")
        report.append("")

    report.append("⏰ 操作建议:")
    report.append("  1. 尾盘入场，次日开盘观察")
    report.append("  2. 达到第一目标位可减仓50%")
    report.append("  3. 严格执行止损纪律")

    return "\n".join(report)
