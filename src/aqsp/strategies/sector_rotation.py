"""板块轮动策略 - A股核心赚钱逻辑。

策略哲学：
- A股的钱在板块里，不在个股里
- 板块热度切换比个股趋势更快、更可靠
- 板块龙头有先发优势，板块二线有补涨空间
- 板块分歧出现时 = 该撤退或换板块

适用场景：3-10 日波段
胜率目标：60%+（追板块龙头）/ 50%+（追补涨）

核心信号：
1. 板块涨幅榜变化（昨日强 vs 今日强）
2. 涨停股板块分布（涨停集中度）
3. 龙头股表现（龙头是否延续强势）
4. 主力资金流入（北向 + 主力净流入）
5. 板块二线启动信号
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List
from collections import defaultdict

import pandas as pd
import numpy as np

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds


@dataclass(frozen=True)
class SectorMetrics:
    """单个板块的综合指标。"""

    sector_id: str
    sector_name: str
    sector_change_pct: float  # 板块涨幅（加权平均）
    sector_change_3d: float  # 3日涨幅
    sector_change_5d: float  # 5日涨幅
    limit_up_count: int  # 板块内涨停数
    limit_up_ratio: float  # 涨停占比
    fund_inflow_rank: int  # 资金流入排名（1=最强）
    leader_stocks: list[str]  # 龙头股
    second_line_stocks: list[str]  # 二线股
    heat_score: float  # 综合热度评分 0-100
    momentum_phase: str  # "启动" / "加速" / "高潮" / "分歧" / "退潮"


@dataclass(frozen=True)
class SectorSignal:
    """板块投资信号。"""

    sector_id: str
    sector_name: str
    signal_type: (
        str  # "leader_follow" / "second_line_catch_up" / "rotation_in" / "rotation_out"
    )
    target_stocks: list[str]
    confidence: float
    timeframe: str  # "1-3_days" / "3-7_days" / "1-2_weeks"
    expected_return: float
    risk_level: str  # "low" / "medium" / "high"
    rationale: list[str]
    risks: list[str]


class SectorRotationStrategy(BaseStrategy):
    """板块轮动策略。

    四种核心信号：

    1. **板块龙头跟随** (leader_follow)
       - 板块热度上升 + 龙头延续涨停 → 买龙头
       - 风险：龙头分歧时立刻撤退
       - 胜率：60-65%

    2. **板块二线补涨** (second_line_catch_up)
       - 龙头已连板 + 二线尚未启动 → 买二线
       - 逻辑：资金溢出效应
       - 胜率：55-60%

    3. **板块轮动入** (rotation_in)
       - 昨日弱板块今日突然爆发（多股涨停）→ 切入
       - 早期信号：第一天试仓，第二天确认加仓
       - 胜率：50-55%（高回报）

    4. **板块退潮** (rotation_out)
       - 龙头炸板 + 板块涨停数减少 → 卖出信号
       - 早识别板块退潮 = 保住利润
    """

    name: str = "sector_rotation"

    def __init__(
        self,
        config: StrategyConfig | None = None,
        thresholds: Thresholds | None = None,
    ):
        self.thresholds = thresholds or load_thresholds()
        config = config or StrategyConfig(name="sector_rotation", enabled=False)
        super().__init__(
            config,
            id="sector_rotation",
            version=self.thresholds.version,
            hypothesis="A股板块轮动有明确节奏，识别热点切换可捕捉短期高收益",
            regime_required=("stable_bull", "volatile_bull", "stable_sideways"),
        )

    def calculate_score(self, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
        """对每只股票评分（基于其所属板块的热度）。

        注：实际生产需要 symbol → sector 的映射表。
        本实现假设 data 中的 DataFrame 含 "sector" 列。
        """
        scores: Dict[str, float] = {}

        # Step 1: 计算所有板块热度
        sector_metrics = self.analyze_sectors(data)

        # Step 2: 每只股票根据所属板块热度评分
        for symbol, df in data.items():
            if df is None or df.empty:
                scores[symbol] = 0.0
                continue

            # 数据有效性校验：脏数据（NaN / 全0 / 负价）直接判 0
            if "close" in df.columns:
                close = df["close"]
                if close.isna().any() or (close <= 0).any():
                    scores[symbol] = 0.0
                    continue

            sector = self._get_stock_sector(df)
            if not sector or sector not in sector_metrics:
                scores[symbol] = 0.3  # 无板块信息，给中性分
                continue

            sector_metric = sector_metrics[sector]

            # 评分 = 板块热度 × 个股相对强度
            sector_heat = sector_metric.heat_score / 100.0
            individual_strength = self._calc_individual_strength(df)

            # 龙头加成
            is_leader = symbol in sector_metric.leader_stocks
            leader_bonus = 0.15 if is_leader else 0.0

            scores[symbol] = min(
                1.0, sector_heat * 0.6 + individual_strength * 0.3 + leader_bonus
            )

        return scores

    def analyze_sectors(
        self, data: Dict[str, pd.DataFrame]
    ) -> Dict[str, SectorMetrics]:
        """分析所有板块的综合指标。"""
        # 按板块聚合股票
        sectors: Dict[str, List[tuple[str, pd.DataFrame]]] = defaultdict(list)

        for symbol, df in data.items():
            if df is None or df.empty:
                continue
            sector = self._get_stock_sector(df)
            if sector:
                sectors[sector].append((symbol, df))

        metrics: Dict[str, SectorMetrics] = {}

        for sector_id, stocks in sectors.items():
            if not stocks:
                continue

            # 1. 计算板块涨幅（等权平均）
            today_changes = []
            three_day_changes = []
            five_day_changes = []
            limit_up_symbols = []

            for symbol, df in stocks:
                df_sorted = df.sort_values("date")
                if len(df_sorted) < 2:
                    continue

                # 今日涨幅
                today_change = self._calc_change_pct(df_sorted, 1)
                today_changes.append(today_change)

                # 3日涨幅
                if len(df_sorted) >= 4:
                    three_day = self._calc_change_pct(df_sorted, 3)
                    three_day_changes.append(three_day)

                # 5日涨幅
                if len(df_sorted) >= 6:
                    five_day = self._calc_change_pct(df_sorted, 5)
                    five_day_changes.append(five_day)

                # 涨停检测
                limit_pct = self._get_limit_pct(symbol)
                if today_change >= (limit_pct - 0.003) * 100:
                    limit_up_symbols.append(symbol)

            sector_change = float(np.mean(today_changes)) if today_changes else 0.0
            sector_change_3d = (
                float(np.mean(three_day_changes)) if three_day_changes else 0.0
            )
            sector_change_5d = (
                float(np.mean(five_day_changes)) if five_day_changes else 0.0
            )

            # 2. 涨停占比
            limit_up_ratio = len(limit_up_symbols) / len(stocks) if stocks else 0.0

            # 3. 龙头识别（涨停最早+量比最大+板数最多）
            leaders = self._identify_leaders(stocks, limit_up_symbols)
            second_lines = self._identify_second_lines(stocks, leaders)

            # 4. 热度评分
            heat = self._calc_heat_score(
                sector_change=sector_change,
                sector_change_3d=sector_change_3d,
                limit_up_count=len(limit_up_symbols),
                limit_up_ratio=limit_up_ratio,
                stock_count=len(stocks),
            )

            # 5. 阶段判断
            phase = self._determine_phase(
                sector_change_3d=sector_change_3d,
                sector_change_5d=sector_change_5d,
                limit_up_count=len(limit_up_symbols),
                today_change=sector_change,
            )

            metrics[sector_id] = SectorMetrics(
                sector_id=sector_id,
                sector_name=sector_id,
                sector_change_pct=round(sector_change, 2),
                sector_change_3d=round(sector_change_3d, 2),
                sector_change_5d=round(sector_change_5d, 2),
                limit_up_count=len(limit_up_symbols),
                limit_up_ratio=round(limit_up_ratio, 3),
                fund_inflow_rank=0,  # 需外部数据
                leader_stocks=leaders[:3],
                second_line_stocks=second_lines[:5],
                heat_score=round(heat, 1),
                momentum_phase=phase,
            )

        return metrics

    def generate_signals(self, data: Dict[str, pd.DataFrame]) -> List[SectorSignal]:
        """生成板块层面的投资信号。"""
        signals: List[SectorSignal] = []
        metrics = self.analyze_sectors(data)

        # 按热度排序
        sorted_metrics = sorted(
            metrics.values(), key=lambda m: m.heat_score, reverse=True
        )

        for metric in sorted_metrics:
            # 信号 1: 启动期 - 板块龙头跟随
            if metric.momentum_phase == "启动" and metric.heat_score > 60:
                signals.append(
                    SectorSignal(
                        sector_id=metric.sector_id,
                        sector_name=metric.sector_name,
                        signal_type="leader_follow",
                        target_stocks=metric.leader_stocks,
                        confidence=min(0.85, metric.heat_score / 100.0),
                        timeframe="3-7_days",
                        expected_return=0.10,
                        risk_level="medium",
                        rationale=[
                            f"板块3日涨幅{metric.sector_change_3d:.1f}%",
                            f"涨停{metric.limit_up_count}只，热度{metric.heat_score:.0f}",
                            "处于启动期，龙头延续概率高",
                        ],
                        risks=["龙头分歧风险", "大盘转弱风险"],
                    )
                )

            # 信号 2: 加速期 - 二线补涨
            elif (
                metric.momentum_phase == "加速"
                and metric.heat_score > 70
                and metric.second_line_stocks
            ):
                signals.append(
                    SectorSignal(
                        sector_id=metric.sector_id,
                        sector_name=metric.sector_name,
                        signal_type="second_line_catch_up",
                        target_stocks=metric.second_line_stocks[:3],
                        confidence=0.65,
                        timeframe="1-3_days",
                        expected_return=0.07,
                        risk_level="medium",
                        rationale=[
                            f"板块涨停{metric.limit_up_count}只，强势",
                            "龙头已涨高，二线补涨空间打开",
                            "资金溢出效应",
                        ],
                        risks=["板块退潮风险", "二线启动假信号"],
                    )
                )

            # 信号 3: 高潮期 - 警示退出
            elif metric.momentum_phase == "高潮":
                signals.append(
                    SectorSignal(
                        sector_id=metric.sector_id,
                        sector_name=metric.sector_name,
                        signal_type="rotation_out",
                        target_stocks=metric.leader_stocks,
                        confidence=0.70,
                        timeframe="immediate",
                        expected_return=-0.05,  # 警示
                        risk_level="high",
                        rationale=[
                            "板块连续5日强势，已到高潮",
                            "龙头炸板风险增大",
                            "建议止盈或减仓",
                        ],
                        risks=["错过最后一波风险"],
                    )
                )

            # 信号 4: 新热点切入
            elif (
                metric.momentum_phase == "启动"
                and metric.sector_change_3d < 2.0
                and metric.sector_change_pct > 4.0  # 今日突然爆发
                and metric.limit_up_count >= 3
            ):
                signals.append(
                    SectorSignal(
                        sector_id=metric.sector_id,
                        sector_name=metric.sector_name,
                        signal_type="rotation_in",
                        target_stocks=metric.leader_stocks,
                        confidence=0.55,
                        timeframe="3-7_days",
                        expected_return=0.12,
                        risk_level="high",
                        rationale=[
                            "新热点爆发（昨日平淡，今日涨停密集）",
                            f"涨停{metric.limit_up_count}只，热度突起",
                            "早期布局空间大",
                        ],
                        risks=["昙花一现风险", "需第二天验证"],
                    )
                )

        return signals

    # ========================================
    # 工具方法
    # ========================================

    def _get_stock_sector(self, df: pd.DataFrame) -> str:
        """从 DataFrame 提取板块信息。"""
        if "sector" in df.columns:
            sector = str(df["sector"].iloc[-1])
            if sector and sector != "nan":
                return sector
        if "industry" in df.columns:
            industry = str(df["industry"].iloc[-1])
            if industry and industry != "nan":
                return industry
        return ""

    def _get_limit_pct(self, symbol: str) -> float:
        """涨停限制。"""
        if symbol.startswith("300") or symbol.startswith("688"):
            return 0.20
        if symbol.startswith("8") or symbol.startswith("4"):
            return 0.30
        return 0.10

    def _calc_change_pct(self, df: pd.DataFrame, days: int) -> float:
        """计算 N 日涨幅（百分比）。"""
        if len(df) < days + 1:
            return 0.0
        current = float(df["close"].iloc[-1])
        past = float(df["close"].iloc[-days - 1])
        if past <= 0:
            return 0.0
        return (current - past) / past * 100

    def _calc_individual_strength(self, df: pd.DataFrame) -> float:
        """个股相对强度评分。"""
        if len(df) < 5:
            return 0.5

        # 5日涨幅
        change_5d = self._calc_change_pct(df, 5)

        # 量比
        if len(df) >= 11:
            today_vol = float(df["volume"].iloc[-1])
            avg_vol = float(df["volume"].iloc[-11:-1].mean())
            vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0
        else:
            vol_ratio = 1.0

        # 评分
        score = 0.0
        if change_5d > 0:
            score += min(0.5, change_5d / 20.0)
        if vol_ratio > 1.2:
            score += min(0.5, (vol_ratio - 1.0) / 3.0)

        return min(1.0, score)

    def _identify_leaders(
        self,
        stocks: List[tuple[str, pd.DataFrame]],
        limit_up_symbols: list[str],
    ) -> list[str]:
        """识别板块龙头（涨停股中选最强）。"""
        if not limit_up_symbols:
            # 没有涨停时，选 5 日涨幅最大的
            scored = []
            for symbol, df in stocks:
                if len(df) >= 6:
                    change_5d = self._calc_change_pct(df.sort_values("date"), 5)
                    scored.append((symbol, change_5d))
            scored.sort(key=lambda x: x[1], reverse=True)
            return [s[0] for s in scored[:3]]

        # 有涨停：从涨停股中按 5 日涨幅排序
        leader_scores = []
        for symbol in limit_up_symbols:
            df = next((d for s, d in stocks if s == symbol), None)
            if df is None:
                continue
            change_5d = self._calc_change_pct(df.sort_values("date"), 5)

            # 量比
            df_sorted = df.sort_values("date")
            if len(df_sorted) >= 11:
                vol_ratio = float(df_sorted["volume"].iloc[-1]) / float(
                    df_sorted["volume"].iloc[-11:-1].mean()
                )
            else:
                vol_ratio = 1.0

            score = change_5d * 0.6 + vol_ratio * 10 * 0.4
            leader_scores.append((symbol, score))

        leader_scores.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in leader_scores[:3]]

    def _identify_second_lines(
        self, stocks: List[tuple[str, pd.DataFrame]], leaders: list[str]
    ) -> list[str]:
        """识别板块二线（非龙头但有补涨潜力）。

        二线特征：
        - 不是龙头
        - 当日涨幅 < 5%（未启动）
        - 但有量能放大迹象
        """
        candidates = []
        for symbol, df in stocks:
            if symbol in leaders:
                continue

            df_sorted = df.sort_values("date")
            if len(df_sorted) < 11:
                continue

            today_change = self._calc_change_pct(df_sorted, 1)
            if today_change > 5:
                continue  # 已经启动

            # 检查量能
            today_vol = float(df_sorted["volume"].iloc[-1])
            avg_vol = float(df_sorted["volume"].iloc[-11:-1].mean())
            vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0

            if vol_ratio > 1.3:
                candidates.append((symbol, vol_ratio))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return [c[0] for c in candidates[:5]]

    def _calc_heat_score(
        self,
        sector_change: float,
        sector_change_3d: float,
        limit_up_count: int,
        limit_up_ratio: float,
        stock_count: int,
    ) -> float:
        """板块热度综合评分（0-100）。

        权重：
        - 当日涨幅：30
        - 3日涨幅：20
        - 涨停数量：30
        - 涨停占比：20
        """
        # 当日涨幅评分
        if sector_change > 5:
            change_score = 30
        elif sector_change > 3:
            change_score = 25
        elif sector_change > 1:
            change_score = 15
        elif sector_change > 0:
            change_score = 8
        else:
            change_score = 0

        # 3日涨幅评分
        if sector_change_3d > 10:
            change_3d_score = 20
        elif sector_change_3d > 5:
            change_3d_score = 15
        elif sector_change_3d > 0:
            change_3d_score = 8
        else:
            change_3d_score = 0

        # 涨停数评分
        if limit_up_count >= 5:
            limit_score = 30
        elif limit_up_count >= 3:
            limit_score = 25
        elif limit_up_count >= 2:
            limit_score = 18
        elif limit_up_count >= 1:
            limit_score = 10
        else:
            limit_score = 0

        # 涨停占比评分
        if limit_up_ratio >= 0.3:
            ratio_score = 20
        elif limit_up_ratio >= 0.15:
            ratio_score = 15
        elif limit_up_ratio >= 0.05:
            ratio_score = 8
        else:
            ratio_score = 0

        return float(change_score + change_3d_score + limit_score + ratio_score)

    def _determine_phase(
        self,
        sector_change_3d: float,
        sector_change_5d: float,
        limit_up_count: int,
        today_change: float,
    ) -> str:
        """判断板块所处阶段。

        阶段定义：
        - 启动：3日涨幅<5%，今日突然爆发（>3%或涨停>=2）
        - 加速：3日涨幅5-15%，涨停数持续>=2
        - 高潮：3日涨幅>15%，单日涨停>=5
        - 分歧：3日涨幅>10%但今日<1%（高位震荡）
        - 退潮：涨停减少，5日涨幅开始转负
        """
        # 退潮
        if sector_change_5d < 0 or (sector_change_5d < sector_change_3d - 2):
            return "退潮"

        # 高潮
        if sector_change_3d > 15 and limit_up_count >= 5:
            return "高潮"

        # 加速
        if 5 <= sector_change_3d <= 15 and limit_up_count >= 2:
            return "加速"

        # 分歧
        if sector_change_3d > 10 and today_change < 1.0:
            return "分歧"

        # 启动
        if sector_change_3d < 5 and (today_change > 3 or limit_up_count >= 2):
            return "启动"

        return "盘整"


def format_sector_signals(signals: List[SectorSignal], top_n: int = 5) -> str:
    """格式化板块信号为可读文本。"""
    if not signals:
        return "📊 板块轮动策略：今日无板块层面信号"

    lines: list[str] = []
    lines.append("板块轮动观察")
    lines.append("=" * 50)
    lines.append(
        f"发现 {len(signals)} 个板块信号，展示前 {min(top_n, len(signals))} 个:"
    )
    lines.append("")

    type_labels = {
        "leader_follow": "龙头带动",
        "second_line_catch_up": "二线扩散",
        "rotation_in": "新热点观察",
        "rotation_out": "⚠️ 退潮警示",
    }

    for i, signal in enumerate(signals[:top_n], 1):
        label = type_labels.get(signal.signal_type, signal.signal_type)
        lines.append(f"【{i}】{signal.sector_name} - {label}")
        lines.append(f"   观察标的: {', '.join(signal.target_stocks[:3])}")
        lines.append(f"   置信度: {signal.confidence:.0%} | 周期: {signal.timeframe}")
        lines.append(
            f"   预期收益: {signal.expected_return:+.1%} | 风险等级: {signal.risk_level}"
        )
        lines.append("   逻辑:")
        for reason in signal.rationale:
            lines.append(f"     • {reason}")
        if signal.risks:
            lines.append("   ⚠️ 风险:")
            for risk in signal.risks:
                lines.append(f"     • {risk}")
        lines.append("")

    lines.append("复核纪律:")
    lines.append("  1. 板块龙头分歧时只保留观察")
    lines.append("  2. 板块涨停数减半 = 退潮信号")
    lines.append("  3. 单板块纸面跟踪不超过 2 只，避免同涨同跌")

    return "\n".join(lines)
