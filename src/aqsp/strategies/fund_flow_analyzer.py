"""资金流入分析模块 - A股主力博弈核心。

资金分析维度：
1. 主力资金净流入（大单+特大单）vs 散户净流出（小单+中单）
2. 北向资金动向（外资风向标）
3. 龙虎榜机构席位 vs 游资席位识别
4. 融资融券变化（杠杆资金动向）
5. 成交结构分析（吸筹/出货模式）

应用场景：
- 主力吸筹识别（缩量阴跌+大单买入 = 吸筹）
- 主力出货识别（放量长上影 + 大单卖出 = 出货）
- 跟随聪明钱（北向+机构席位重叠 = 高确定性）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Any

import pandas as pd


@dataclass(frozen=True)
class FundFlowSnapshot:
    """资金流入快照。"""

    symbol: str
    date: date
    main_inflow: float  # 主力净流入（万元）
    super_large_inflow: float  # 特大单净流入
    large_inflow: float  # 大单净流入
    medium_outflow: float  # 中单净流出
    small_outflow: float  # 小单净流出
    main_inflow_ratio: float  # 主力流入占比（vs 总成交）
    north_holding_change: float  # 北向持股变化


@dataclass(frozen=True)
class LongHuBangRecord:
    """龙虎榜记录。"""

    symbol: str
    date: date
    net_buy: float  # 净买入（万元）
    institutional_seats: int  # 机构席位数
    institutional_net_buy: float  # 机构净买入
    top1_seat_name: str  # 第一席位名称
    top1_is_institutional: bool  # 第一席位是否机构
    famous_speculator_present: bool  # 是否有知名游资席位
    seat_details: list[dict]  # 席位明细


@dataclass(frozen=True)
class FundFlowSignal:
    """资金流分析信号。"""

    symbol: str
    signal_type: str  # "main_force_accumulating" / "main_force_distributing" / "smart_money_following" / "retail_chasing_high"
    confidence: float
    main_inflow_5d: float
    north_holding_5d_change: float
    inst_seat_count_30d: int  # 30日内机构席位上榜次数
    rationale: list[str]
    risks: list[str]


class FundFlowAnalyzer:
    """资金流分析器。

    核心模式识别：

    1. **主力吸筹模式** (main_force_accumulating)
       - 价格震荡或缓跌
       - 主力净流入连续5日为正
       - 散户净流出（恐慌交筹码）
       - 关键：缩量阴线但主力买入 = 强吸筹

    2. **主力出货模式** (main_force_distributing)
       - 价格创新高但量能不持续
       - 主力净流出（高位减仓）
       - 散户净流入（追高接盘）
       - 关键：放量长上影 + 主力净流出 = 高位派发

    3. **聪明钱跟随** (smart_money_following)
       - 北向资金连续买入
       - 龙虎榜机构席位上榜
       - 主力大单净流入
       - 多重信号叠加 = 高胜率

    4. **散户追高警示** (retail_chasing_high)
       - 价格涨停或大涨
       - 主力净流出（出货）
       - 散户净流入（接盘）
       - 提示：风险高，不宜追高
    """

    def analyze_flow_pattern(
        self,
        snapshots: List[FundFlowSnapshot],
        price_data: pd.DataFrame,
    ) -> Optional[FundFlowSignal]:
        """分析单股资金流模式。"""
        if not snapshots or price_data.empty:
            return None

        # 取最近 5 日数据
        recent = snapshots[-5:]
        if len(recent) < 3:
            return None

        symbol = recent[-1].symbol

        # 1. 计算主力 5 日净流入
        main_5d = sum(s.main_inflow for s in recent)
        north_5d = sum(s.north_holding_change for s in recent)

        # 2. 主力流入持续性
        positive_days = sum(1 for s in recent if s.main_inflow > 0)
        consistency = positive_days / len(recent)

        # 3. 价格走势
        price_data_sorted = price_data.sort_values("date").tail(5)
        if len(price_data_sorted) < 3:
            return None

        price_change = (
            float(price_data_sorted["close"].iloc[-1])
            - float(price_data_sorted["close"].iloc[0])
        ) / float(price_data_sorted["close"].iloc[0])

        # 4. 量能分析
        recent_vol = float(price_data_sorted["volume"].iloc[-3:].mean())
        prev_vol = (
            float(price_data.sort_values("date")["volume"].iloc[-15:-5].mean())
            if len(price_data) >= 15 else recent_vol
        )
        vol_ratio = recent_vol / prev_vol if prev_vol > 0 else 1.0

        # 模式 1: 主力吸筹（价格震荡，主力持续买入）
        if (
            abs(price_change) < 0.05
            and main_5d > 0
            and consistency >= 0.6
        ):
            confidence = min(0.85, consistency + (main_5d / 10000) * 0.1)
            return FundFlowSignal(
                symbol=symbol,
                signal_type="main_force_accumulating",
                confidence=confidence,
                main_inflow_5d=main_5d,
                north_holding_5d_change=north_5d,
                inst_seat_count_30d=0,
                rationale=[
                    f"价格5日变化{price_change:+.1%}（震荡）",
                    f"主力5日净流入{main_5d/10000:.0f}万",
                    f"主力买入持续性{consistency:.0%}",
                    "经典吸筹模式" if vol_ratio < 1.2 else "活跃吸筹",
                ],
                risks=[
                    "主力意图判断需结合龙虎榜",
                    "吸筹时间可能较长",
                ],
            )

        # 模式 2: 主力出货（价格新高，主力卖出）
        if (
            price_change > 0.10
            and main_5d < 0
            and vol_ratio > 1.5
        ):
            confidence = min(0.80, abs(main_5d) / 50000 + 0.3)
            return FundFlowSignal(
                symbol=symbol,
                signal_type="main_force_distributing",
                confidence=confidence,
                main_inflow_5d=main_5d,
                north_holding_5d_change=north_5d,
                inst_seat_count_30d=0,
                rationale=[
                    f"价格5日涨{price_change:.1%}（创新高）",
                    f"主力5日净流出{abs(main_5d)/10000:.0f}万",
                    f"量比{vol_ratio:.1f}倍（放量）",
                    "高位派发特征",
                ],
                risks=[
                    "持有者建议止盈",
                    "追高有接盘风险",
                ],
            )

        # 模式 3: 聪明钱跟随（北向 + 主力共振）
        if (
            north_5d > 1000000  # 100万股+
            and main_5d > 0
            and price_change > 0
        ):
            confidence = min(0.85, 0.5 + (north_5d / 5000000) * 0.3 + (main_5d / 30000) * 0.05)
            return FundFlowSignal(
                symbol=symbol,
                signal_type="smart_money_following",
                confidence=confidence,
                main_inflow_5d=main_5d,
                north_holding_5d_change=north_5d,
                inst_seat_count_30d=0,
                rationale=[
                    f"北向5日增持{north_5d/10000:.0f}万股",
                    f"主力5日净流入{main_5d/10000:.0f}万",
                    f"价格上涨{price_change:.1%}",
                    "外资+主力共振，高确定性",
                ],
                risks=[
                    "需关注北向资金转向风险",
                ],
            )

        # 模式 4: 散户追高（涨停但主力出货）
        if (
            price_change > 0.07
            and main_5d < -10000
        ):
            return FundFlowSignal(
                symbol=symbol,
                signal_type="retail_chasing_high",
                confidence=0.70,
                main_inflow_5d=main_5d,
                north_holding_5d_change=north_5d,
                inst_seat_count_30d=0,
                rationale=[
                    f"价格涨{price_change:.1%}（散户追涨）",
                    f"主力净流出{abs(main_5d)/10000:.0f}万",
                    "高位接盘风险大",
                ],
                risks=[
                    "🔴 不建议追高",
                    "回调风险显著",
                ],
            )

        return None

    def analyze_longhubang(
        self, lhb_records: List[LongHuBangRecord]
    ) -> Dict[str, Any]:
        """分析龙虎榜数据。

        返回每只股票的：
        - 30日机构席位次数
        - 是否有知名游资
        - 净买入累计
        """
        result: Dict[str, Dict[str, Any]] = {}

        for record in lhb_records:
            symbol = record.symbol
            if symbol not in result:
                result[symbol] = {
                    "inst_count_30d": 0,
                    "speculator_count_30d": 0,
                    "net_buy_30d": 0.0,
                    "inst_net_buy_30d": 0.0,
                    "famous_speculators": set(),
                }

            data = result[symbol]
            data["inst_count_30d"] += record.institutional_seats
            data["net_buy_30d"] += record.net_buy
            data["inst_net_buy_30d"] += record.institutional_net_buy

            if record.famous_speculator_present:
                data["speculator_count_30d"] += 1

        # 转换 set 为 list
        for symbol_data in result.values():
            symbol_data["famous_speculators"] = list(symbol_data["famous_speculators"])

        return result

    def smart_money_score(
        self,
        symbol: str,
        flow_signal: Optional[FundFlowSignal],
        lhb_data: Optional[Dict[str, Any]],
    ) -> float:
        """聪明钱综合评分（0-100）。

        权重：
        - 资金流模式：40%
        - 龙虎榜机构席位：30%
        - 北向持仓变化：20%
        - 游资关注度：10%
        """
        score = 0.0

        # 资金流模式
        if flow_signal:
            if flow_signal.signal_type == "smart_money_following":
                score += 40 * flow_signal.confidence
            elif flow_signal.signal_type == "main_force_accumulating":
                score += 30 * flow_signal.confidence
            elif flow_signal.signal_type == "main_force_distributing":
                score -= 30 * flow_signal.confidence
            elif flow_signal.signal_type == "retail_chasing_high":
                score -= 20 * flow_signal.confidence

        # 龙虎榜
        if lhb_data:
            inst_count = lhb_data.get("inst_count_30d", 0)
            inst_net = lhb_data.get("inst_net_buy_30d", 0)
            if inst_count >= 3 and inst_net > 50000:  # 5000万
                score += 30
            elif inst_count >= 2:
                score += 20
            elif inst_count >= 1:
                score += 10

            # 游资关注
            speculator_count = lhb_data.get("speculator_count_30d", 0)
            if speculator_count >= 2:
                score += 10
            elif speculator_count >= 1:
                score += 5

        # 北向
        if flow_signal:
            if flow_signal.north_holding_5d_change > 5000000:  # 500万股
                score += 20
            elif flow_signal.north_holding_5d_change > 1000000:
                score += 10

        return max(0.0, min(100.0, score))


def format_fund_flow_signals(signals: List[FundFlowSignal], top_n: int = 5) -> str:
    """格式化资金流信号。"""
    if not signals:
        return "💰 资金流分析：今日无显著信号"

    type_labels = {
        "main_force_accumulating": "🟢 主力吸筹",
        "main_force_distributing": "🔴 主力出货",
        "smart_money_following": "💎 聪明钱跟随",
        "retail_chasing_high": "⚠️ 散户追高",
    }

    lines: list[str] = []
    lines.append("💰 资金流分析报告")
    lines.append("=" * 50)
    lines.append(f"发现 {len(signals)} 个信号，重点 Top {min(top_n, len(signals))}:")
    lines.append("")

    for i, signal in enumerate(signals[:top_n], 1):
        label = type_labels.get(signal.signal_type, signal.signal_type)
        lines.append(f"【{i}】{signal.symbol} - {label}")
        lines.append(f"   置信度: {signal.confidence:.0%}")
        lines.append(f"   主力5日净流入: {signal.main_inflow_5d/10000:+.0f}万")
        if signal.north_holding_5d_change != 0:
            lines.append(f"   北向5日变化: {signal.north_holding_5d_change/10000:+.0f}万股")
        lines.append("   分析:")
        for r in signal.rationale:
            lines.append(f"     • {r}")
        if signal.risks:
            lines.append("   ⚠️ 风险:")
            for r in signal.risks:
                lines.append(f"     • {r}")
        lines.append("")

    return "\n".join(lines)
