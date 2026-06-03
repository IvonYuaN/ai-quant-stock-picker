"""事件驱动策略 - A股特色的事件反应交易。

策略哲学：
- A股是情绪市，事件催化往往带来短期暴利机会
- 重组复牌、高送转、业绩预增、政策利好是四大经典事件
- 事件后的「价格行为模式」可作为事件代理信号（无需实时事件数据）

适用场景：1-5 日（事件冲击波）
胜率目标：55%+（事件确认后）

⚠️ 数据依赖说明：
理想的事件驱动需要实时事件数据源（公告/财报/政策）。
当前实现用「技术形态」作为事件代理信号：
- 长期停牌后复牌 → 用「数据中断 + 复牌放量」识别
- 业绩预增 → 用「突然放量大涨脱离均线」近似
- 真实事件数据接入见文件末 TODO
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from aqsp.strategies.base import BaseStrategy, StrategyConfig
from aqsp.strategies.thresholds import Thresholds, load_thresholds


@dataclass(frozen=True)
class EventSignal:
    """事件驱动信号。"""

    symbol: str
    name: str
    event_type: str  # "resume_trading" / "earnings_surge" / "sudden_breakout" / "policy_boost"
    score: float
    current_price: float
    entry_price: float
    stop_loss: float
    take_profit: float
    position_pct: float
    holding_period: str
    confidence: float
    reasons: list[str]
    risks: list[str]
    needs_external_data: list[str]  # 需要外部数据验证的维度


class EventDrivenStrategy(BaseStrategy):
    """事件驱动策略。

    四种事件模式（基于技术形态代理）：

    1. **复牌接力** (resume_trading)
       - 检测：数据时间出现明显断层（停牌）后恢复
       - 复牌后放量涨停/大涨 → 接力机会
       - 风险：复牌补跌也可能

    2. **业绩兑现** (earnings_surge)
       - 检测：突然放量大涨，脱离震荡区间
       - 近似业绩预增/超预期行情
       - 需外部数据：实际财报数据

    3. **突发放量异动** (sudden_breakout)
       - 检测：长期横盘后突然放量突破
       - 可能有未公开消息/事件驱动
       - 资金提前埋伏特征

    4. **政策板块联动** (policy_boost)
       - 检测：整板块同步异动（需板块数据）
       - 政策利好的板块性拉升
       - 需外部数据：板块归属 + 政策事件
    """

    name: str = "event_driven"

    # 策略自带参数
    SUSPEND_GAP_DAYS = 5  # 数据断层视为停牌的天数阈值
    SURGE_VOLUME_RATIO = 2.5  # 异动放量倍数
    SURGE_PRICE_PCT = 0.07  # 异动涨幅阈值
    CONSOLIDATION_DAYS = 20  # 横盘判定天数
    CONSOLIDATION_RANGE = 0.12  # 横盘振幅阈值
    MIN_SCORE = 0.55

    def __init__(
        self,
        config: StrategyConfig | None = None,
        thresholds: Thresholds | None = None,
    ):
        self.thresholds = thresholds or load_thresholds()
        # 默认 enabled=False：宪法红线，未经 walk-forward 双门验证不上线
        config = config or StrategyConfig(name="event_driven", enabled=False)
        super().__init__(
            config,
            id="event_driven",
            version=self.thresholds.version,
            hypothesis="A股事件催化（复牌/业绩/政策）带来短期价格冲击，事件后价格行为模式可捕捉超额收益",
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
        df = df.sort_values("date").tail(40)
        if len(df) < 10:
            return 0.0

        # 数据有效性校验：脏数据（NaN / 全0 / 负价）直接判 0
        close = df["close"]
        if close.isna().any() or (close <= 0).any():
            return 0.0

        # 取四种事件模式的最高分
        resume_score = self._score_resume_trading(df)
        surge_score = self._score_earnings_surge(df)
        breakout_score = self._score_sudden_breakout(df)

        return max(resume_score, surge_score, breakout_score)

    def _score_resume_trading(self, df: pd.DataFrame) -> float:
        """复牌接力评分：检测数据断层后的放量。"""
        if "date" not in df.columns or len(df) < 5:
            return 0.0

        # 检测日期断层
        try:
            dates = pd.to_datetime(df["date"])
        except Exception:
            return 0.0

        date_diffs = dates.diff().dt.days.dropna()
        if date_diffs.empty:
            return 0.0

        # 最近是否有明显断层（停牌）
        max_gap = float(date_diffs.iloc[-min(10, len(date_diffs)):].max())
        if max_gap < self.SUSPEND_GAP_DAYS:
            return 0.0  # 没有停牌迹象

        # 复牌后的表现（最后一日）
        last_close = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else last_close
        change = (last_close - prev_close) / prev_close if prev_close > 0 else 0

        # 复牌放量
        if len(df) >= 6:
            last_vol = float(df["volume"].iloc[-1])
            avg_vol = float(df["volume"].iloc[-6:-1].mean())
            vol_ratio = last_vol / avg_vol if avg_vol > 0 else 0
        else:
            vol_ratio = 0

        score = 0.0
        if change > 0.05 and vol_ratio > 1.5:
            score = 0.9  # 复牌放量大涨，强接力信号
        elif change > 0.02:
            score = 0.6
        elif change > 0:
            score = 0.4

        return score

    def _score_earnings_surge(self, df: pd.DataFrame) -> float:
        """业绩兑现评分：突然放量大涨脱离均线。"""
        if len(df) < 21:
            return 0.0

        last_close = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2])
        change = (last_close - prev_close) / prev_close if prev_close > 0 else 0

        if change < self.SURGE_PRICE_PCT:
            return 0.0

        # 放量
        last_vol = float(df["volume"].iloc[-1])
        avg_vol = float(df["volume"].iloc[-21:-1].mean())
        vol_ratio = last_vol / avg_vol if avg_vol > 0 else 0

        if vol_ratio < self.SURGE_VOLUME_RATIO:
            return 0.0

        # 脱离均线（突破式上涨）
        ma20 = float(df["close"].rolling(20).mean().iloc[-1])
        deviation = (last_close - ma20) / ma20 if ma20 > 0 else 0

        score = 0.0
        if deviation > 0.08 and vol_ratio > 3.0:
            score = 0.85  # 放巨量脱离均线，业绩兑现特征
        elif deviation > 0.05:
            score = 0.65
        else:
            score = 0.5

        return score

    def _score_sudden_breakout(self, df: pd.DataFrame) -> float:
        """突发异动评分：长期横盘后突然放量突破。"""
        if len(df) < self.CONSOLIDATION_DAYS + 2:
            return 0.0

        # 判断前期是否横盘
        consolidation = df["close"].iloc[-self.CONSOLIDATION_DAYS - 1 : -1]
        c_high = float(consolidation.max())
        c_low = float(consolidation.min())
        if c_low <= 0:
            return 0.0
        c_range = (c_high - c_low) / c_low

        if c_range > self.CONSOLIDATION_RANGE:
            return 0.0  # 前期不是横盘

        # 当日突破横盘上沿 + 放量
        last_close = float(df["close"].iloc[-1])
        last_vol = float(df["volume"].iloc[-1])
        avg_vol = float(df["volume"].iloc[-self.CONSOLIDATION_DAYS - 1 : -1].mean())
        vol_ratio = last_vol / avg_vol if avg_vol > 0 else 0

        if last_close > c_high and vol_ratio > 2.0:
            return 0.8  # 横盘突破+放量，疑似事件驱动
        if last_close > c_high:
            return 0.5
        return 0.0

    def generate_signals(self, data: Dict[str, pd.DataFrame]) -> List[EventSignal]:
        """生成事件驱动信号。"""
        signals: List[EventSignal] = []

        for symbol, df in data.items():
            if df is None or df.empty:
                continue

            df_sorted = df.sort_values("date").tail(40)
            if len(df_sorted) < 10:
                continue

            # 评估各事件模式
            resume = self._score_resume_trading(df_sorted)
            surge = self._score_earnings_surge(df_sorted)
            breakout = self._score_sudden_breakout(df_sorted)

            event_scores = [
                ("resume_trading", resume, "1-3_days"),
                ("earnings_surge", surge, "2-5_days"),
                ("sudden_breakout", breakout, "1-5_days"),
            ]
            event_scores.sort(key=lambda x: x[1], reverse=True)
            event_type, best_score, holding = event_scores[0]

            if best_score < self.MIN_SCORE:
                continue

            current_price = float(df_sorted["close"].iloc[-1])
            entry, stop, target = self._calc_targets(event_type, current_price)
            position = self._suggest_position(event_type, best_score)

            reasons, risks, needs_data = self._collect_info(df_sorted, event_type)

            signals.append(
                EventSignal(
                    symbol=symbol,
                    name=str(df_sorted["name"].iloc[-1]) if "name" in df_sorted.columns else symbol,
                    event_type=event_type,
                    score=round(best_score * 100, 1),
                    current_price=round(current_price, 2),
                    entry_price=round(entry, 2),
                    stop_loss=round(stop, 2),
                    take_profit=round(target, 2),
                    position_pct=round(position, 2),
                    holding_period=holding,
                    confidence=round(best_score, 2),
                    reasons=reasons,
                    risks=risks,
                    needs_external_data=needs_data,
                )
            )

        signals.sort(key=lambda x: x.score, reverse=True)
        return signals

    def _calc_targets(self, event_type: str, current: float) -> tuple[float, float, float]:
        if event_type == "resume_trading":
            # 复牌：波动大，宽止损
            return current, current * 0.93, current * 1.15
        if event_type == "earnings_surge":
            # 业绩：追高风险，紧止损
            return current, current * 0.95, current * 1.10
        # sudden_breakout
        return current, current * 0.95, current * 1.12

    def _suggest_position(self, event_type: str, score: float) -> float:
        # 事件驱动风险高，仓位保守
        if event_type == "resume_trading":
            return 0.10  # 复牌不确定性大
        if score > 0.7:
            return 0.15
        return 0.08

    def _collect_info(
        self, df: pd.DataFrame, event_type: str
    ) -> tuple[list[str], list[str], list[str]]:
        reasons: list[str] = []
        risks: list[str] = []
        needs_data: list[str] = []

        if event_type == "resume_trading":
            reasons.append("检测到停牌后复牌，放量异动")
            risks.append("复牌方向不确定，可能补跌")
            needs_data.append("停牌原因（重组/违规）需公告数据确认")

        elif event_type == "earnings_surge":
            last_change = (float(df["close"].iloc[-1]) - float(df["close"].iloc[-2])) / float(df["close"].iloc[-2])
            reasons.append(f"放巨量大涨{last_change:.1%}，脱离均线")
            reasons.append("疑似业绩超预期/重大利好")
            risks.append("可能是消息兑现，追高风险")
            needs_data.append("实际财报数据需 tushare/akshare 接入")

        elif event_type == "sudden_breakout":
            reasons.append("长期横盘后放量突破")
            reasons.append("资金提前埋伏，疑似事件驱动")
            risks.append("无消息确认，可能是诱多")
            needs_data.append("龙虎榜/公告确认是否真有事件")

        return reasons, risks, needs_data


def format_event_signals(signals: List[EventSignal], top_n: int = 5) -> str:
    """格式化事件驱动信号。"""
    if not signals:
        return "📰 事件驱动策略：今日无事件异动信号"

    type_labels = {
        "resume_trading": "🔄 复牌接力",
        "earnings_surge": "📊 业绩兑现",
        "sudden_breakout": "⚡ 突发异动",
        "policy_boost": "🏛️ 政策联动",
    }

    lines: list[str] = []
    lines.append("📰 事件驱动策略推荐")
    lines.append("=" * 50)
    lines.append(f"发现 {len(signals)} 只事件异动股，推荐 Top {min(top_n, len(signals))}:")
    lines.append("")

    for i, signal in enumerate(signals[:top_n], 1):
        label = type_labels.get(signal.event_type, signal.event_type)
        lines.append(f"【{i}】{signal.symbol} {signal.name} - {label}")
        lines.append(f"   得分: {signal.score:.1f} | 置信度: {signal.confidence:.0%}")
        lines.append(f"   现价: {signal.current_price:.2f} | 周期: {signal.holding_period}")
        lines.append(f"   止损: {signal.stop_loss:.2f} | 目标: {signal.take_profit:.2f} | 仓位: {signal.position_pct:.0%}")
        lines.append("   理由:")
        for r in signal.reasons:
            lines.append(f"     • {r}")
        if signal.risks:
            lines.append("   ⚠️ 风险:")
            for r in signal.risks:
                lines.append(f"     • {r}")
        if signal.needs_external_data:
            lines.append("   📡 需外部数据确认:")
            for d in signal.needs_external_data:
                lines.append(f"     • {d}")
        lines.append("")

    lines.append("⚡ 操作纪律:")
    lines.append("  1. 事件驱动风险高，仓位务必保守")
    lines.append("  2. 复牌股不确定性大，轻仓试探")
    lines.append("  3. 务必结合公告/财报确认事件真实性")

    return "\n".join(lines)


# ============================================================
# TODO: 真实事件数据接入（下一阶段）
# ============================================================
# 当前用技术形态做事件代理。要做真正的事件驱动，需接入：
#
# 1. 停复牌数据：akshare ak.stock_tfp_em()（停复牌）
# 2. 业绩预告：akshare ak.stock_yjyg_em()（业绩预告）
# 3. 高送转：akshare ak.stock_fhps_em()（分红送转）
# 4. 龙虎榜：akshare ak.stock_lhb_detail_em()
# 5. 政策事件：需新闻/公告 NLP（可选 LLM，走 llm_call_or_fallback wrapper）
#
# 接入后改造方向：
# - calculate_score 增加事件数据加权
# - generate_signals 用真实事件类型替代技术代理
# - 事件日历：提前 N 天预警已知事件（如解禁、分红除权）
