"""涨停板梯度策略 - A股短线核心策略。

策略哲学：
- 涨停是市场情绪和资金最强烈的信号
- 连板（连续涨停）是龙头股最直接证据
- 接力策略：低位首板 -> 二板 -> 三板，每板信号强度不同
- 配合分级止损和量价配合判断真假涨停

适用场景：短线（1-5日）持仓
胜率目标：60%+（涨停板池）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd
import numpy as np

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds


@dataclass(frozen=True)
class LimitUpSignal:
    """涨停板信号数据。"""

    symbol: str
    name: str
    board_count: int  # 连板数（1=首板, 2=二板, 3=三板...）
    limit_type: str  # "T字板" / "一字板" / "回封" / "强势封板" / "弱势封板"
    seal_strength: float  # 封单强度 0-1
    volume_ratio: float  # 量比（vs 20日均量）
    pre_market_strength: float  # 开盘前竞价强度
    next_day_open_prob: float  # 预测次日开盘概率（基于历史回测）
    entry_strategy: str  # "竞价抢筹" / "开盘强势" / "盘中回踩" / "尾盘买入"
    position_pct: float  # 建议仓位
    stop_loss_pct: float  # 止损百分比（相对买入价）
    target_pct: float  # 目标涨幅
    risk_signals: List[str]  # 风险信号


class LimitUpLadderStrategy(BaseStrategy):
    """涨停板梯度策略 - 识别连板龙头和接力机会。

    评分维度：
    1. 连板数（4维度评分）：首板/二板/三板/N板
    2. 封单强度（5档）：一字板/T字板/强势封板/正常/弱势
    3. 量价配合：缩量封板（强）/ 放量封板（一般）/ 放量炸板（弱）
    4. 板块联动：板块龙头加成
    5. 时间分布：开盘封板（强）/ 下午封板（一般）

    分级止损：
    - 首板/二板：3% 硬止损
    - 三板及以上：2% 硬止损（高位风险大）
    - 一字板：1% 硬止损（无博弈空间）
    """

    name: str = "limit_up_ladder"

    LIMIT_UP_PCT = 0.10  # 10% 涨停（主板）
    LIMIT_UP_PCT_KCB = 0.20  # 20% 涨停（科创板/创业板）

    def __init__(
        self,
        config: StrategyConfig | None = None,
        thresholds: Thresholds | None = None,
    ):
        self.thresholds = thresholds or load_thresholds()
        config = config or StrategyConfig(name="limit_up_ladder", enabled=False)
        super().__init__(
            config,
            id="limit_up_ladder",
            version=self.thresholds.version,
            hypothesis="A股涨停板蕴含市场最强情绪信号，连板龙头股短期延续概率高",
            regime_required=("stable_bull", "volatile_bull"),
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        for symbol, df in data.items():
            if df is None or df.empty:
                scores[symbol] = 0.0
                continue
            scores[symbol] = self._calculate_single_score(df, symbol)
        return scores

    def _calculate_single_score(self, df: pd.DataFrame, symbol: str) -> float:
        """单标的综合评分（0-1）。"""
        df = df.sort_values("date").tail(20)
        if len(df) < 5:
            return 0.0

        # 数据有效性校验：脏数据（NaN / 全0 / 负价）直接判 0
        close = df["close"]
        if close.isna().any() or (close <= 0).any():
            return 0.0

        # 1. 检测连板数
        board_count = self._count_consecutive_limits(df, symbol)
        if board_count == 0:
            return 0.0

        # 2. 评分维度
        board_score = self._score_board_count(board_count)
        seal_score = self._score_seal_strength(df)
        volume_score = self._score_volume_pattern(df, board_count)
        time_score = self._score_close_time_pattern(df)

        # 3. 风险扣减
        risk_penalty = self._calc_risk_penalty(df, board_count)

        # 综合（加权）
        weights = (0.35, 0.25, 0.25, 0.15)
        final = (
            board_score * weights[0]
            + seal_score * weights[1]
            + volume_score * weights[2]
            + time_score * weights[3]
        ) - risk_penalty

        return max(0.0, min(1.0, final))

    def _count_consecutive_limits(self, df: pd.DataFrame, symbol: str) -> int:
        """统计连续涨停天数（从最新一天往前数）。"""
        limit_pct = self._get_limit_pct(symbol)
        closes = df["close"].values
        if len(closes) < 2:
            return 0

        # 计算每日涨跌幅
        returns = np.diff(closes) / closes[:-1]
        # 从最新一天往前看，统计连续涨停
        consecutive = 0
        # 涨停容差：偏差0.5%以内视为涨停（处理浮点和数据精度）
        threshold = limit_pct - 0.003
        for r in reversed(returns):
            if r >= threshold:
                consecutive += 1
            else:
                break
        return consecutive

    def _get_limit_pct(self, symbol: str) -> float:
        """根据symbol判断涨停限制。
        300xxx创业板 / 688xxx科创板 = 20%
        北交所 = 30%
        主板 = 10%
        """
        if symbol.startswith("300") or symbol.startswith("688"):
            return 0.20
        if symbol.startswith("8") or symbol.startswith("4"):
            return 0.30  # 北交所
        return 0.10

    def _score_board_count(self, board_count: int) -> float:
        """连板数评分。

        逻辑：
        - 首板：0.5（试探仓位）
        - 二板：0.8（连板确认）
        - 三板：1.0（强势龙头）
        - 四板及以上：0.85（高位风险增大）
        """
        if board_count == 1:
            return 0.5
        if board_count == 2:
            return 0.8
        if board_count == 3:
            return 1.0
        if board_count == 4:
            return 0.9
        if board_count >= 5:
            # 5板及以上风险骤增（容易闷杀）
            return max(0.3, 0.85 - (board_count - 4) * 0.15)
        return 0.0

    def _score_seal_strength(self, df: pd.DataFrame) -> float:
        """封单强度评分（基于价格波动和成交结构）。

        评估指标：
        - 收盘价 == 最高价（强封）
        - 振幅小（强封）
        - 尾盘走稳（强封）

        理想结构：
        - 一字板：最理想，0.95
        - 强势封板（开盘后封死）：0.85
        - 回封：0.6
        - 弱势封板（震荡）：0.4
        """
        last = df.iloc[-1]

        high = float(last.get("high", 0))
        low = float(last.get("low", 0))
        close = float(last.get("close", 0))
        open_p = float(last.get("open", 0))

        if high == 0 or open_p == 0:
            return 0.5

        # 振幅
        amplitude = (high - low) / open_p if open_p > 0 else 1.0
        # 收盘 == 最高 == 涨停价
        is_sealed = abs(close - high) < 0.001 * close

        if amplitude < 0.01 and is_sealed:
            return 0.95  # 一字板
        if amplitude < 0.03 and is_sealed:
            return 0.85  # 强势封板
        if is_sealed and amplitude < 0.05:
            return 0.7  # 正常封板
        if is_sealed:
            return 0.5  # 大振幅封板（回封）
        return 0.2  # 没封住

    def _score_volume_pattern(self, df: pd.DataFrame, board_count: int) -> float:
        """量价配合评分。

        关键洞察：
        - 首板：放量优（市场注意，0.8）
        - 二板：缩量好（惜售，0.9）
        - 三板：缩量极好（封板牢，1.0）
        - 三板+放量：警惕分歧（0.5）
        """
        if len(df) < 10:
            return 0.5

        current_vol = float(df["volume"].iloc[-1])
        avg_vol = float(df["volume"].iloc[-11:-1].mean())
        if avg_vol <= 0:
            return 0.5

        ratio = current_vol / avg_vol

        if board_count == 1:
            # 首板：放量1.5-3倍最好
            if 1.5 <= ratio <= 3.0:
                return 1.0
            if 1.0 <= ratio < 1.5:
                return 0.7
            if 3.0 < ratio <= 5.0:
                return 0.6
            return 0.3

        if board_count >= 2:
            # 连板：缩量好
            if ratio < 0.7:
                return 1.0
            if ratio < 1.0:
                return 0.85
            if ratio < 1.5:
                return 0.6
            # 连板放量 = 分歧
            return 0.3

        return 0.5

    def _score_close_time_pattern(self, df: pd.DataFrame) -> float:
        """收盘时间模式（基于日线推断）。

        日线推断：
        - 开盘=最高=收盘=涨停价 → 早盘封板（强）
        - 开盘<涨停价，收盘=涨停价 → 盘中封板
        - 收盘<最高，但> open*1.09 → 尾盘炸板回封（弱）
        """
        last = df.iloc[-1]
        open_p = float(last.get("open", 0))
        high = float(last.get("high", 0))
        close = float(last.get("close", 0))

        if open_p == 0:
            return 0.5

        # 开盘即涨停
        if abs(open_p - high) < 0.001 * high and abs(close - high) < 0.001 * high:
            return 1.0
        # 收盘 == 最高（盘中封板）
        if abs(close - high) < 0.001 * high:
            return 0.7
        # 触及涨停但没封住
        return 0.3

    def _calc_risk_penalty(self, df: pd.DataFrame, board_count: int) -> float:
        """风险扣减。

        风险因素：
        - 高位风险：连板数过多
        - 量能异常：突然大幅放量（资金出逃）
        - 价格高位：距离前期高点近
        """
        penalty = 0.0

        # 高位风险（连板太多）
        if board_count >= 5:
            penalty += 0.15
        if board_count >= 7:
            penalty += 0.2

        # 量能异常：今日 vs 昨日
        if len(df) >= 2:
            today_vol = float(df["volume"].iloc[-1])
            yesterday_vol = float(df["volume"].iloc[-2])
            if yesterday_vol > 0:
                vol_change = today_vol / yesterday_vol
                if vol_change > 3.0:
                    penalty += 0.15  # 突然大幅放量警惕
                elif vol_change > 5.0:
                    penalty += 0.25  # 极度放量（高位风险）

        return penalty

    def generate_signals(self, data: Dict[str, pd.DataFrame]) -> List[LimitUpSignal]:
        """生成涨停板买入信号。"""
        signals: List[LimitUpSignal] = []

        for symbol, df in data.items():
            if df is None or df.empty:
                continue

            score = self._calculate_single_score(df, symbol)
            if score < 0.5:
                continue

            df_sorted = df.sort_values("date").tail(20)
            board_count = self._count_consecutive_limits(df_sorted, symbol)
            if board_count == 0:
                continue

            limit_type = self._classify_limit_type(df_sorted)
            seal_strength = self._score_seal_strength(df_sorted)
            volume_ratio = self._calc_volume_ratio(df_sorted)
            position_pct = self._suggest_position(board_count, score)
            stop_loss_pct = self._suggest_stop_loss(board_count, limit_type)
            target_pct = self._suggest_target(board_count, limit_type)

            risk_signals = []
            if board_count >= 5:
                risk_signals.append(f"高位连板（{board_count}板），随时炸板风险")
            if volume_ratio > 3.0 and board_count >= 2:
                risk_signals.append(f"放量{volume_ratio:.1f}倍，警惕主力出货")
            if seal_strength < 0.5:
                risk_signals.append("封单不强，次日开盘谨慎")

            signals.append(
                LimitUpSignal(
                    symbol=symbol,
                    name=str(df_sorted["name"].iloc[-1])
                    if "name" in df_sorted.columns
                    else symbol,
                    board_count=board_count,
                    limit_type=limit_type,
                    seal_strength=round(seal_strength, 2),
                    volume_ratio=round(volume_ratio, 2),
                    pre_market_strength=0.0,  # 需要实时数据接入
                    next_day_open_prob=self._estimate_open_prob(
                        board_count, seal_strength, volume_ratio
                    ),
                    entry_strategy=self._suggest_entry(board_count, seal_strength),
                    position_pct=round(position_pct, 2),
                    stop_loss_pct=round(stop_loss_pct, 3),
                    target_pct=round(target_pct, 3),
                    risk_signals=risk_signals,
                )
            )

        signals.sort(key=lambda x: (x.board_count, x.seal_strength), reverse=True)
        return signals

    def _classify_limit_type(self, df: pd.DataFrame) -> str:
        """涨停板类型分类。"""
        last = df.iloc[-1]
        open_p = float(last.get("open", 0))
        high = float(last.get("high", 0))
        low = float(last.get("low", 0))
        close = float(last.get("close", 0))

        if open_p == 0:
            return "未知"

        amplitude = (high - low) / open_p

        if abs(open_p - high) < 0.001 * high and abs(close - high) < 0.001 * high:
            if amplitude < 0.005:
                return "一字板"
            return "T字板"
        if amplitude < 0.03:
            return "强势封板"
        if abs(close - high) < 0.001 * high:
            return "回封"
        return "弱势封板"

    def _calc_volume_ratio(self, df: pd.DataFrame) -> float:
        """量比计算。"""
        if len(df) < 11:
            return 0.0
        current = float(df["volume"].iloc[-1])
        avg = float(df["volume"].iloc[-11:-1].mean())
        return current / avg if avg > 0 else 0.0

    def _suggest_position(self, board_count: int, score: float) -> float:
        """建议仓位（基于板数和评分）。

        仓位逻辑：
        - 高分（>0.8）首板：20%试仓
        - 高分（>0.8）二板：30%核心仓
        - 高分（>0.8）三板：15%（已涨高了）
        - 四板及以上：5-10%（高风险）
        """
        if score < 0.5:
            return 0.0
        if board_count == 1:
            return 0.20 if score > 0.7 else 0.10
        if board_count == 2:
            return 0.30 if score > 0.7 else 0.15
        if board_count == 3:
            return 0.15 if score > 0.7 else 0.08
        # 4板及以上
        return 0.05

    def _suggest_stop_loss(self, board_count: int, limit_type: str) -> float:
        """建议止损（相对买入价的百分比）。

        分级止损：
        - 一字板：1%（无博弈空间，破1%即逃）
        - T字板：2%
        - 首板：3%
        - 二板：3%
        - 三板及以上：2%（高位严格止损）
        """
        if limit_type == "一字板":
            return 0.01
        if limit_type == "T字板":
            return 0.02
        if board_count <= 2:
            return 0.03
        return 0.02  # 三板以上

    def _suggest_target(self, board_count: int, limit_type: str) -> float:
        """建议目标涨幅。

        - 首板：目标 +7-10%（再涨停）
        - 二板：目标 +10-15%（追求三板）
        - 三板及以上：目标 +5-8%（高位见好就收）
        """
        if board_count == 1:
            return 0.08
        if board_count == 2:
            return 0.12
        return 0.06

    def _estimate_open_prob(
        self, board_count: int, seal_strength: float, volume_ratio: float
    ) -> float:
        """次日开盘高开概率估算。

        简化模型：
        - 一字板/强势封板 + 缩量：85% 高开
        - 普通封板 + 正常量：65%
        - 弱势封板/放量：40%
        """
        base = 0.5
        if seal_strength > 0.85:
            base += 0.25
        elif seal_strength > 0.6:
            base += 0.10

        if volume_ratio < 0.8 and board_count >= 2:
            base += 0.15  # 连板缩量+
        elif volume_ratio > 2.5:
            base -= 0.15  # 放量警惕

        if board_count == 1:
            base -= 0.05  # 首板次日波动大
        elif board_count >= 4:
            base -= 0.15  # 高位回调风险

        return max(0.1, min(0.95, base))

    def _suggest_entry(self, board_count: int, seal_strength: float) -> str:
        """建议入场方式。

        - 一字板/强封：9:15 集合竞价抢筹
        - 强势：9:30 开盘强势
        - 一般：盘中回踩
        - 弱势：尾盘观察
        """
        if seal_strength > 0.85:
            return "9:15 竞价抢筹"
        if seal_strength > 0.7 and board_count >= 2:
            return "9:30 开盘强势"
        if board_count == 1:
            return "盘中回踩MA5"
        return "尾盘观察"


def format_limit_up_signals(signals: List[LimitUpSignal], top_n: int = 5) -> str:
    """格式化涨停板信号为可读文本（备用，主要用结构化数据通过renderer输出）。"""
    if not signals:
        return "📊 涨停板梯度策略：今日未发现符合条件的涨停股"

    lines: list[str] = []
    lines.append("涨停板梯度观察")
    lines.append("=" * 50)
    lines.append(
        f"发现 {len(signals)} 只待复核涨停股，展示前 {min(top_n, len(signals))} 只:"
    )
    lines.append("")

    for i, signal in enumerate(signals[:top_n], 1):
        lines.append(f"【{i}】{signal.symbol} {signal.name} - {signal.board_count}板")
        lines.append(
            f"   板型: {signal.limit_type} (封单强度 {signal.seal_strength:.0%})"
        )
        lines.append(f"   量比: {signal.volume_ratio:.1f}x")
        lines.append(f"   次日高开概率: {signal.next_day_open_prob:.0%}")
        lines.append(
            f"   观察方式: {signal.entry_strategy} | 参考仓位 {signal.position_pct:.0%}"
        )
        lines.append(
            f"   止损: -{signal.stop_loss_pct:.1%} | 目标: +{signal.target_pct:.1%}"
        )
        if signal.risk_signals:
            lines.append("   ⚠️ 风险:")
            for risk in signal.risk_signals:
                lines.append(f"     • {risk}")
        lines.append("")

    lines.append("⚡ 操作纪律:")
    lines.append("  1. 严格执行分级止损，破位即出")
    lines.append("  2. 连板高度依赖大盘情绪，市场转弱立即减仓")
    lines.append("  3. 不追高位连板，5板以上风险陡增")

    return "\n".join(lines)
