"""日内交易策略集合 - T+0模拟+尾盘买入+反弹策略。

包含三个核心日内策略：
1. T+0模拟（盘中波段）：捕捉日内3-5%振幅
2. 尾盘竞价策略：14:30-15:00 的最后机会
3. 极值反转：跌停反弹/超跌捡漏
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd
import numpy as np

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds


@dataclass(frozen=True)
class IntradaySignal:
    symbol: str
    name: str
    strategy_type: str  # "T+0_swing" / "closing_auction" / "oversold_rebound"
    score: float
    entry_price: float
    stop_loss: float
    take_profit: float
    timeframe: str  # "intraday" / "1-2_days" / "3-5_days"
    confidence: float
    reasons: list[str]
    risks: list[str]


class IntradayTradeStrategy(BaseStrategy):
    """日内交易策略 - 适用于A股 T+1 制度下的相对短线机会。

    虽然A股是T+1，但可以用以下方式模拟T+0：
    1. 底仓 + T模式：底仓打底，盘中加仓，第二天卖出
    2. 滚动操作：今天买强势股，明天卖出
    3. 极端日内机会：超跌反弹、尾盘启动

    评估三个核心模式。
    """

    name: str = "intraday_trade"

    def __init__(
        self,
        config: StrategyConfig | None = None,
        thresholds: Thresholds | None = None,
    ):
        self.thresholds = thresholds or load_thresholds()
        config = config or StrategyConfig(name="intraday_trade", enabled=False)
        super().__init__(
            config,
            id="intraday_trade",
            version=self.thresholds.version,
            hypothesis="日内极值往往是次日反转或延续的关键信号，配合量价可获短线收益",
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        for symbol, df in data.items():
            if df is None or df.empty:
                scores[symbol] = 0.0
                continue
            scores[symbol] = self._calculate_max_score(df)
        return scores

    def _calculate_max_score(self, df: pd.DataFrame) -> float:
        """取三种模式中的最高分。"""
        df_sorted = df.sort_values("date").tail(30)
        if len(df_sorted) < 10:
            return 0.0

        # 数据有效性校验：脏数据（NaN / 全0 / 负价）直接判 0
        close = df_sorted["close"]
        if close.isna().any() or (close <= 0).any():
            return 0.0

        score_t0 = self._score_t0_pattern(df_sorted)
        score_closing = self._score_closing_pattern(df_sorted)
        score_rebound = self._score_oversold_rebound(df_sorted)

        return max(score_t0, score_closing, score_rebound)

    # ========================================
    # 模式 1: T+0 波段（适合做底仓+滚动）
    # ========================================

    def _score_t0_pattern(self, df: pd.DataFrame) -> float:
        """T+0 滚动模式评分。

        识别条件：
        - 日内振幅 > 4%（足够空间）
        - 量能配合（放量震荡）
        - 主力做T痕迹（高开冲高回落，低吸再拉升）
        - 多日重复模式（说明主力在做T）
        """
        if len(df) < 5:
            return 0.0

        # 最近5日平均振幅
        recent = df.iloc[-5:]
        amplitudes = []
        for _, row in recent.iterrows():
            high = float(row.get("high", 0))
            low = float(row.get("low", 0))
            open_p = float(row.get("open", 1))
            if open_p > 0:
                amplitudes.append((high - low) / open_p)
        avg_amp = np.mean(amplitudes) if amplitudes else 0.0

        if avg_amp < 0.04:
            return 0.0  # 振幅不够

        # 振幅评分
        amp_score = min(1.0, avg_amp / 0.08)  # 8%以上满分

        # 量能评分（持续放量）
        recent_vol = float(df["volume"].iloc[-5:].mean())
        prev_vol = float(df["volume"].iloc[-15:-5].mean()) if len(df) >= 15 else recent_vol
        vol_ratio = recent_vol / prev_vol if prev_vol > 0 else 0
        vol_score = min(1.0, vol_ratio / 1.5) if vol_ratio > 1.0 else 0.3

        # 趋势评分（横盘震荡为佳，强趋势中不做T）
        closes = df["close"].iloc[-10:].values
        trend = (closes[-1] - closes[0]) / closes[0] if closes[0] > 0 else 0
        # 横盘±5%以内最好
        if abs(trend) < 0.05:
            trend_score = 1.0
        elif abs(trend) < 0.10:
            trend_score = 0.6
        else:
            trend_score = 0.2

        return 0.4 * amp_score + 0.3 * vol_score + 0.3 * trend_score

    # ========================================
    # 模式 2: 尾盘竞价策略（14:30-15:00 的最后机会）
    # ========================================

    def _score_closing_pattern(self, df: pd.DataFrame) -> float:
        """尾盘启动模式评分。

        识别条件：
        - 当日收盘价 > 开盘价（阳线）
        - 收盘价接近最高价（无上影线或短上影）
        - 当日成交量持续放大（尾盘加速）
        - 关键：基于日线，假设收盘前1小时是关键
        """
        if len(df) < 1:
            return 0.0

        last = df.iloc[-1]
        open_p = float(last.get("open", 0))
        high = float(last.get("high", 0))
        low = float(last.get("low", 0))
        close = float(last.get("close", 0))

        if open_p == 0 or high == 0:
            return 0.0

        # 当日涨幅
        change_pct = (close - open_p) / open_p

        if change_pct < 0.01:
            return 0.0  # 阴线或平盘

        # 1. 收盘强势：收盘价接近最高价
        close_to_high = (close - low) / (high - low) if high > low else 0
        close_strength = close_to_high  # 0-1

        # 2. 涨幅评分（2%-5%最佳，超过容易追高）
        if 0.02 <= change_pct <= 0.05:
            change_score = 1.0
        elif 0.01 <= change_pct < 0.02:
            change_score = 0.5
        elif 0.05 < change_pct <= 0.08:
            change_score = 0.7
        else:
            change_score = 0.3

        # 3. 量价配合
        if len(df) >= 10:
            today_vol = float(df["volume"].iloc[-1])
            avg_vol = float(df["volume"].iloc[-11:-1].mean())
            vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0
            if 1.2 <= vol_ratio <= 2.5:
                vol_score = 1.0
            elif vol_ratio < 1.2:
                vol_score = 0.5
            else:
                vol_score = 0.6  # 放量太大可能是出货
        else:
            vol_score = 0.5

        # 4. 上影线检查
        body = abs(close - open_p)
        upper_shadow = high - max(close, open_p)
        shadow_ratio = upper_shadow / body if body > 0 else 10
        # 上影线 < body 的50%才合格
        shadow_score = 1.0 if shadow_ratio < 0.5 else max(0, 1.0 - shadow_ratio)

        return (
            0.30 * close_strength
            + 0.25 * change_score
            + 0.25 * vol_score
            + 0.20 * shadow_score
        )

    # ========================================
    # 模式 3: 超跌反弹（跌停后反弹机会）
    # ========================================

    def _score_oversold_rebound(self, df: pd.DataFrame) -> float:
        """超跌反弹模式评分。

        识别条件：
        - 近期跌幅 > 15%（有反弹空间）
        - RSI < 30（超卖）
        - 最近一日放量止跌（变化信号）
        - 不在下跌趋势中段（避免接飞刀）
        """
        if len(df) < 15:
            return 0.0

        closes = df["close"].values

        # 1. 近期跌幅评估（10日跌幅）
        if len(closes) >= 10:
            ten_day_change = (closes[-1] - closes[-10]) / closes[-10]
        else:
            ten_day_change = 0

        # 跌幅 > 15% 才符合
        if ten_day_change > -0.10:
            return 0.0

        # 2. RSI计算
        rsi = self._calculate_rsi(df["close"], period=14)
        if rsi is None:
            return 0.0

        # RSI < 30 是超卖
        if rsi >= 40:
            return 0.0

        rsi_score = (30 - min(30, rsi)) / 30  # RSI越低评分越高

        # 3. 最近一日是否放量止跌
        if len(df) < 5:
            return 0.0

        last_close = float(df["close"].iloc[-1])
        last_open = float(df["open"].iloc[-1])
        last_vol = float(df["volume"].iloc[-1])
        prev_vol = float(df["volume"].iloc[-5:-1].mean())

        # 当日阳线 + 放量
        is_bullish = last_close > last_open
        vol_ratio = last_vol / prev_vol if prev_vol > 0 else 0
        stop_signal = 1.0 if (is_bullish and vol_ratio > 1.5) else 0.3

        # 4. 跌幅评分（跌得越多反弹空间越大，但要避免极端）
        decline_score = min(1.0, abs(ten_day_change) / 0.20)
        if ten_day_change < -0.30:
            decline_score *= 0.7  # 跌太多有问题，扣分

        return (
            0.30 * rsi_score
            + 0.35 * stop_signal
            + 0.35 * decline_score
        )

    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> float | None:
        if len(prices) < period + 1:
            return None
        deltas = prices.diff().dropna()
        gains = deltas.where(deltas > 0, 0)
        losses = -deltas.where(deltas < 0, 0)
        avg_gain = gains.rolling(period).mean().iloc[-1]
        avg_loss = losses.rolling(period).mean().iloc[-1]
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def generate_signals(
        self, data: Dict[str, pd.DataFrame]
    ) -> List[IntradaySignal]:
        """生成日内交易信号。"""
        signals: List[IntradaySignal] = []

        for symbol, df in data.items():
            if df is None or df.empty:
                continue

            df_sorted = df.sort_values("date").tail(30)
            if len(df_sorted) < 10:
                continue

            # 评估三种模式
            score_t0 = self._score_t0_pattern(df_sorted)
            score_closing = self._score_closing_pattern(df_sorted)
            score_rebound = self._score_oversold_rebound(df_sorted)

            scores = [
                ("T+0_swing", score_t0, "intraday"),
                ("closing_auction", score_closing, "1-2_days"),
                ("oversold_rebound", score_rebound, "3-5_days"),
            ]
            scores.sort(key=lambda x: x[1], reverse=True)

            best_strategy, best_score, timeframe = scores[0]

            if best_score < 0.6:
                continue

            current_price = float(df_sorted["close"].iloc[-1])
            entry_price, stop_loss, take_profit = self._calc_targets(
                df_sorted, best_strategy, current_price
            )

            reasons, risks = self._collect_signal_info(
                df_sorted, best_strategy, best_score
            )

            signals.append(
                IntradaySignal(
                    symbol=symbol,
                    name=str(df_sorted["name"].iloc[-1]) if "name" in df_sorted.columns else symbol,
                    strategy_type=best_strategy,
                    score=round(best_score * 100, 1),
                    entry_price=round(entry_price, 2),
                    stop_loss=round(stop_loss, 2),
                    take_profit=round(take_profit, 2),
                    timeframe=timeframe,
                    confidence=round(best_score, 2),
                    reasons=reasons,
                    risks=risks,
                )
            )

        signals.sort(key=lambda x: x.score, reverse=True)
        return signals

    def _calc_targets(
        self, df: pd.DataFrame, strategy_type: str, current: float
    ) -> tuple[float, float, float]:
        """根据策略类型计算入场、止损、目标。"""
        # ATR
        atr = self._calc_atr(df, 14)

        if strategy_type == "T+0_swing":
            # T+0：紧止损，小目标
            entry = current
            stop_loss = current - atr * 1.0
            take_profit = current + atr * 2.0
        elif strategy_type == "closing_auction":
            # 尾盘：次日开盘强势继续，止损2%
            entry = current
            stop_loss = current * 0.98
            take_profit = current * 1.05
        else:  # oversold_rebound
            # 超跌反弹：宽止损，看反弹5-10%
            entry = current
            stop_loss = current * 0.97  # 3% 止损（已经跌了很多）
            take_profit = current * 1.08
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

    def _collect_signal_info(
        self, df: pd.DataFrame, strategy: str, score: float
    ) -> tuple[list[str], list[str]]:
        """收集信号原因和风险。"""
        reasons: list[str] = []
        risks: list[str] = []

        if strategy == "T+0_swing":
            recent_amp = self._calc_avg_amplitude(df, 5)
            reasons.append(f"日内振幅{recent_amp:.1%}，适合滚动操作")
            reasons.append("横盘震荡，主力做T痕迹明显")
            risks.append("T+1 制度下需有底仓，盈亏比降低")

        elif strategy == "closing_auction":
            last = df.iloc[-1]
            change = (float(last["close"]) - float(last["open"])) / float(last["open"])
            reasons.append(f"当日涨幅 {change:.1%}，收盘强势")
            reasons.append("放量阳线，多头氛围浓")
            risks.append("尾盘买入次日开盘风险高")

        else:  # oversold_rebound
            ten_day = (float(df["close"].iloc[-1]) - float(df["close"].iloc[-10])) / float(df["close"].iloc[-10])
            rsi = self._calculate_rsi(df["close"])
            reasons.append(f"10日跌幅 {ten_day:.1%}，超跌")
            if rsi:
                reasons.append(f"RSI {rsi:.1f}，超卖区域")
            reasons.append("放量止跌，反弹信号确认")
            risks.append("接飞刀风险，需结合大盘判断")

        return reasons, risks

    def _calc_avg_amplitude(self, df: pd.DataFrame, days: int) -> float:
        recent = df.iloc[-days:]
        amps = []
        for _, row in recent.iterrows():
            o = float(row.get("open", 0))
            h = float(row.get("high", 0))
            low = float(row.get("low", 0))
            if o > 0:
                amps.append((h - low) / o)
        return float(np.mean(amps)) if amps else 0.0


def format_intraday_signals(signals: List[IntradaySignal], top_n: int = 5) -> str:
    """格式化日内交易信号。"""
    if not signals:
        return "📊 日内交易策略：今日无符合条件的标的"

    type_labels = {
        "T+0_swing": "🔄 T+0滚动",
        "closing_auction": "🕐 尾盘启动",
        "oversold_rebound": "📉 超跌反弹",
    }

    lines: list[str] = []
    lines.append("⚡ 日内交易策略推荐")
    lines.append("=" * 50)
    lines.append(f"发现 {len(signals)} 只标的，推荐 Top {min(top_n, len(signals))}:")
    lines.append("")

    for i, signal in enumerate(signals[:top_n], 1):
        label = type_labels.get(signal.strategy_type, signal.strategy_type)
        lines.append(f"【{i}】{signal.symbol} {signal.name} - {label}")
        lines.append(f"   得分: {signal.score:.1f} | 置信度: {signal.confidence:.0%}")
        lines.append(f"   入场: {signal.entry_price:.2f} | 止损: {signal.stop_loss:.2f} | 目标: {signal.take_profit:.2f}")
        lines.append(f"   周期: {signal.timeframe}")
        lines.append("   理由:")
        for r in signal.reasons:
            lines.append(f"     • {r}")
        if signal.risks:
            lines.append("   ⚠️ 风险:")
            for r in signal.risks:
                lines.append(f"     • {r}")
        lines.append("")

    lines.append("⚡ 操作纪律:")
    lines.append("  1. T+0 需底仓配合，严守 T+1 制度")
    lines.append("  2. 尾盘买入次日开盘需果断")
    lines.append("  3. 超跌反弹是抢反弹，快进快出")

    return "\n".join(lines)
