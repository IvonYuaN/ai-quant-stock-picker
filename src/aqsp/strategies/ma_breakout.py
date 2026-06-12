"""均线突破策略 - 经典强势股识别 + 量价共振。

策略哲学：
- 趋势是朋友：站稳长期均线的票才值得做
- 多周期共振：MA5/MA20/MA60 同向才是真趋势
- 量价配合：突破必须放量，否则是假突破
- 回踩确认：突破后回踩不破，才是上车点

适用场景：3-15 日波段（趋势跟随）
胜率目标：55%+（趋势确认后）

经典买点：
1. 突破买点：放量突破前高/平台
2. 回踩买点：突破后回踩 MA10/MA20 不破
3. 加速买点：均线多头排列后的二次加速
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd
import numpy as np

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds


@dataclass(frozen=True)
class MABreakoutSignal:
    """均线突破信号。"""

    symbol: str
    name: str
    signal_type: str  # "breakout" / "pullback_confirm" / "acceleration"
    score: float
    current_price: float
    entry_price: float
    stop_loss: float
    take_profit: float
    position_pct: float
    ma_alignment: str  # "多头排列" / "初步多头" / "纠缠"
    volume_confirm: bool
    reasons: list[str]
    risks: list[str]


class MABreakoutStrategy(BaseStrategy):
    """均线突破策略。

    评分维度（4维）：
    1. 趋势确认（35%）：站稳MA60 + MA多头排列
    2. 突破质量（30%）：突破前高/平台 + 突破幅度
    3. 量价配合（25%）：突破放量 + 持续性
    4. 回踩健康度（10%）：回踩不破关键均线

    三种买点：
    - breakout：当日放量突破（激进）
    - pullback_confirm：突破后回踩确认（稳健，推荐）
    - acceleration：多头排列后二次加速（趋势中继）
    """

    name: str = "ma_breakout"

    # 策略自带参数（不依赖 thresholds.yaml）
    MA_SHORT = 5
    MA_MID = 20
    MA_LONG = 60
    VOLUME_BREAKOUT_RATIO = 1.5  # 突破放量倍数
    BREAKOUT_LOOKBACK = 20  # 突破前高回看天数
    MIN_SCORE = 0.55

    def __init__(
        self,
        config: StrategyConfig | None = None,
        thresholds: Thresholds | None = None,
    ):
        self.thresholds = thresholds or load_thresholds()
        # 默认 enabled=False：宪法红线，未经 walk-forward 双门验证不上线
        config = config or StrategyConfig(name="ma_breakout", enabled=False)
        super().__init__(
            config,
            id="ma_breakout",
            version=self.thresholds.version,
            hypothesis="站稳长期均线且多周期共振的强势股，突破后趋势延续概率高，量价配合可过滤假突破",
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
        df = df.sort_values("date").tail(self.MA_LONG + 10)
        if len(df) < self.MA_LONG:
            return 0.0

        # 数据有效性校验：脏数据（NaN / 全0 / 负价）直接判 0，避免误评分
        close = df["close"]
        if close.isna().any() or (close <= 0).any():
            return 0.0

        trend_score = self._score_trend(df)
        breakout_score = self._score_breakout(df)
        volume_score = self._score_volume(df)
        pullback_score = self._score_pullback_health(df)

        final = (
            trend_score * 0.35
            + breakout_score * 0.30
            + volume_score * 0.25
            + pullback_score * 0.10
        )
        return max(0.0, min(1.0, final))

    def _score_trend(self, df: pd.DataFrame) -> float:
        """趋势确认评分：站稳MA60 + 多头排列。"""
        closes = df["close"]
        if len(closes) < self.MA_LONG:
            return 0.0

        ma5 = float(closes.rolling(self.MA_SHORT).mean().iloc[-1])
        ma20 = float(closes.rolling(self.MA_MID).mean().iloc[-1])
        ma60 = float(closes.rolling(self.MA_LONG).mean().iloc[-1])
        current = float(closes.iloc[-1])

        score = 0.0

        # 站稳 MA60（中长期趋势向上）
        if current > ma60:
            score += 0.4
            # MA60 本身向上（斜率为正）
            ma60_prev = float(closes.rolling(self.MA_LONG).mean().iloc[-6])
            if ma60 > ma60_prev:
                score += 0.2

        # 多头排列：MA5 > MA20 > MA60
        if ma5 > ma20 > ma60:
            score += 0.4  # 完美多头排列
        elif ma5 > ma20:
            score += 0.2  # 初步多头

        return min(1.0, score)

    def _score_breakout(self, df: pd.DataFrame) -> float:
        """突破质量评分：突破前期高点/平台。"""
        if len(df) < self.BREAKOUT_LOOKBACK + 1:
            return 0.0

        closes = df["close"].values
        highs = df["high"].values
        current = closes[-1]

        # 突破前 N 日高点（不含当日）
        prev_high = float(np.max(highs[-self.BREAKOUT_LOOKBACK - 1 : -1]))
        if prev_high <= 0:
            return 0.0

        breakout_pct = (current - prev_high) / prev_high

        if breakout_pct > 0.03:
            return 1.0  # 强势突破
        if breakout_pct > 0.01:
            return 0.8  # 有效突破
        if breakout_pct > 0:
            return 0.6  # 微突破
        if breakout_pct > -0.02:
            return 0.4  # 临近突破（蓄势）
        return 0.1

    def _score_volume(self, df: pd.DataFrame) -> float:
        """量价配合评分：突破放量。"""
        if len(df) < 21:
            return 0.5

        current_vol = float(df["volume"].iloc[-1])
        avg_vol = float(df["volume"].iloc[-21:-1].mean())
        if avg_vol <= 0:
            return 0.5

        ratio = current_vol / avg_vol

        if ratio >= self.VOLUME_BREAKOUT_RATIO:
            # 进一步检查是否价涨量增（健康）
            price_up = float(df["close"].iloc[-1]) > float(df["close"].iloc[-2])
            if price_up:
                return 1.0
            return 0.5  # 放量但价跌（警惕）
        if ratio >= 1.0:
            return 0.6
        return 0.3  # 缩量突破（可能假突破）

    def _score_pullback_health(self, df: pd.DataFrame) -> float:
        """回踩健康度：回踩不破关键均线。"""
        closes = df["close"]
        if len(closes) < self.MA_MID:
            return 0.5

        ma10 = float(closes.rolling(10).mean().iloc[-1])
        ma20 = float(closes.rolling(self.MA_MID).mean().iloc[-1])
        current = float(closes.iloc[-1])
        low = float(df["low"].iloc[-1])

        # 当日最低没破 MA10 = 健康
        if low >= ma10:
            return 1.0
        # 收盘站回 MA10 上方
        if current >= ma10:
            return 0.7
        # 跌破 MA10 但守住 MA20
        if current >= ma20:
            return 0.4
        return 0.1

    def generate_signals(self, data: Dict[str, pd.DataFrame]) -> List[MABreakoutSignal]:
        """生成均线突破买入信号。"""
        signals: List[MABreakoutSignal] = []

        for symbol, df in data.items():
            if df is None or df.empty:
                continue

            df_sorted = df.sort_values("date").tail(self.MA_LONG + 10)
            if len(df_sorted) < self.MA_LONG:
                continue

            score = self._calculate_single_score(df_sorted)
            if score < self.MIN_SCORE:
                continue

            signal_type = self._determine_signal_type(df_sorted)
            ma_alignment = self._classify_ma_alignment(df_sorted)
            volume_confirm = self._is_volume_confirmed(df_sorted)

            current_price = float(df_sorted["close"].iloc[-1])
            entry, stop, target = self._calc_targets(
                df_sorted, signal_type, current_price
            )
            position = self._suggest_position(signal_type, score)

            reasons, risks = self._collect_info(
                df_sorted, signal_type, ma_alignment, volume_confirm
            )

            signals.append(
                MABreakoutSignal(
                    symbol=symbol,
                    name=str(df_sorted["name"].iloc[-1])
                    if "name" in df_sorted.columns
                    else symbol,
                    signal_type=signal_type,
                    score=round(score * 100, 1),
                    current_price=round(current_price, 2),
                    entry_price=round(entry, 2),
                    stop_loss=round(stop, 2),
                    take_profit=round(target, 2),
                    position_pct=round(position, 2),
                    ma_alignment=ma_alignment,
                    volume_confirm=volume_confirm,
                    reasons=reasons,
                    risks=risks,
                )
            )

        signals.sort(key=lambda x: x.score, reverse=True)
        return signals

    def _determine_signal_type(self, df: pd.DataFrame) -> str:
        """判断买点类型。"""
        closes = df["close"].values
        current = closes[-1]
        prev = closes[-2] if len(closes) >= 2 else current

        ma10 = float(df["close"].rolling(10).mean().iloc[-1])
        breakout_score = self._score_breakout(df)
        volume_confirm = self._is_volume_confirmed(df)

        # 当日放量突破
        if breakout_score >= 0.8 and volume_confirm:
            return "breakout"
        # 回踩MA10附近企稳
        low = float(df["low"].iloc[-1])
        if low <= ma10 * 1.01 and current >= ma10:
            return "pullback_confirm"
        # 多头排列中继加速
        if current > prev and self._classify_ma_alignment(df) == "多头排列":
            return "acceleration"
        return "breakout"

    def _classify_ma_alignment(self, df: pd.DataFrame) -> str:
        closes = df["close"]
        ma5 = float(closes.rolling(self.MA_SHORT).mean().iloc[-1])
        ma20 = float(closes.rolling(self.MA_MID).mean().iloc[-1])
        ma60 = float(closes.rolling(self.MA_LONG).mean().iloc[-1])

        if ma5 > ma20 > ma60:
            return "多头排列"
        if ma5 > ma20:
            return "初步多头"
        return "纠缠"

    def _is_volume_confirmed(self, df: pd.DataFrame) -> bool:
        if len(df) < 21:
            return False
        current_vol = float(df["volume"].iloc[-1])
        avg_vol = float(df["volume"].iloc[-21:-1].mean())
        return avg_vol > 0 and current_vol / avg_vol >= self.VOLUME_BREAKOUT_RATIO

    def _calc_targets(
        self, df: pd.DataFrame, signal_type: str, current: float
    ) -> tuple[float, float, float]:
        atr = self._calc_atr(df)
        ma20 = float(df["close"].rolling(self.MA_MID).mean().iloc[-1])

        if signal_type == "breakout":
            entry = current
            stop_loss = max(current - atr * 2.0, ma20 * 0.98)  # ATR 或 MA20 防守
            take_profit = current + atr * 4.0
        elif signal_type == "pullback_confirm":
            entry = current
            stop_loss = current * 0.95  # 回踩买点止损 5%
            take_profit = current * 1.12
        else:  # acceleration
            entry = current
            stop_loss = current - atr * 1.5
            take_profit = current + atr * 3.0
        return entry, stop_loss, take_profit

    def _calc_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        if len(df) < period:
            return float(df["high"].iloc[-1] - df["low"].iloc[-1]) * 0.5
        high = df["high"].iloc[-period:]
        low = df["low"].iloc[-period:]
        close = df["close"].iloc[-period:]
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        return float(tr.mean())

    def _suggest_position(self, signal_type: str, score: float) -> float:
        """分级建仓。"""
        if signal_type == "breakout":
            # 突破买点：试仓
            return 0.20 if score > 0.7 else 0.10
        if signal_type == "pullback_confirm":
            # 回踩确认：可加仓（最稳）
            return 0.35 if score > 0.7 else 0.20
        # acceleration
        return 0.15

    def _collect_info(
        self,
        df: pd.DataFrame,
        signal_type: str,
        ma_alignment: str,
        volume_confirm: bool,
    ) -> tuple[list[str], list[str]]:
        reasons: list[str] = []
        risks: list[str] = []

        type_desc = {
            "breakout": "放量突破前高",
            "pullback_confirm": "突破后回踩MA10确认（稳健买点）",
            "acceleration": "多头排列二次加速",
        }
        reasons.append(type_desc.get(signal_type, signal_type))
        reasons.append(f"均线状态: {ma_alignment}")

        if volume_confirm:
            reasons.append("突破放量确认")
        else:
            risks.append("量能不足，警惕假突破")

        current = float(df["close"].iloc[-1])
        ma60 = float(df["close"].rolling(self.MA_LONG).mean().iloc[-1])
        if current > ma60:
            reasons.append(f"站稳MA60（{ma60:.2f}），中长期趋势向上")
        else:
            risks.append("未站稳MA60，趋势不明")

        if ma_alignment == "纠缠":
            risks.append("均线纠缠，方向不明，建议等待")

        return reasons, risks


def format_ma_breakout_signals(signals: List[MABreakoutSignal], top_n: int = 5) -> str:
    """格式化均线突破信号。"""
    if not signals:
        return "📊 均线突破策略：今日无符合条件的突破股"

    type_labels = {
        "breakout": "🚀 放量突破",
        "pullback_confirm": "✅ 回踩确认（稳健）",
        "acceleration": "📈 加速中继",
    }

    lines: list[str] = []
    lines.append("均线突破观察")
    lines.append("=" * 50)
    lines.append(
        f"发现 {len(signals)} 只待复核候选，展示前 {min(top_n, len(signals))} 只:"
    )
    lines.append("")

    for i, signal in enumerate(signals[:top_n], 1):
        label = type_labels.get(signal.signal_type, signal.signal_type)
        lines.append(f"【{i}】{signal.symbol} {signal.name} - {label}")
        lines.append(f"   得分: {signal.score:.1f} | 均线: {signal.ma_alignment}")
        lines.append(
            f"   现价: {signal.current_price:.2f} | 参考价: {signal.entry_price:.2f}"
        )
        lines.append(
            f"   止损: {signal.stop_loss:.2f} | 目标: {signal.take_profit:.2f}"
        )
        lines.append(f"   参考仓位: {signal.position_pct:.0%}")
        lines.append("   理由:")
        for r in signal.reasons:
            lines.append(f"     • {r}")
        if signal.risks:
            lines.append("   ⚠️ 风险:")
            for r in signal.risks:
                lines.append(f"     • {r}")
        lines.append("")

    lines.append("复核纪律:")
    lines.append("  1. 优先回踩确认买点（胜率最高）")
    lines.append("  2. 突破买点试仓，回踩不破再加仓")
    lines.append("  3. 跌破MA20减仓，跌破MA60清仓")

    return "\n".join(lines)
