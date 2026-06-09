"""Briefing 数据结构定义 - 结构化数据模型，避免反向解析 markdown。

宪法要求：数据流正向，不反向解析自己生成的 markdown。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aqsp.core.types import PickResult
from aqsp.portfolio.manager import PortfolioDecisionSummary
from aqsp.research.summary import ResearchSummary
from aqsp.briefing.debate import DebateResult


@dataclass(frozen=True)
class Pick:
    """单个选股结果的结构化数据。"""

    symbol: str
    name: str
    score: float
    rating: str
    strategies: tuple[str, ...]
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    metrics: dict[str, Any]
    date: str
    ideal_buy: float
    stop_loss: float
    take_profit: float
    position: str

    @classmethod
    def from_pick_result(cls, pick: PickResult) -> Pick:
        """从 PickResult 转换。"""
        return cls(
            symbol=pick.symbol,
            name=pick.name,
            score=pick.score,
            rating=pick.rating,
            strategies=tuple(pick.strategies),
            reasons=tuple(pick.reasons),
            risks=tuple(pick.risks),
            metrics=dict(pick.metrics),
            date=pick.date,
            ideal_buy=pick.ideal_buy,
            stop_loss=pick.stop_loss,
            take_profit=pick.take_profit,
            position=pick.position,
        )


@dataclass(frozen=True)
class RegimeInfo:
    """市场态势信息。"""

    regime: str
    description: str
    circuit_breaker_triggered: bool
    circuit_breaker_reason: str


@dataclass(frozen=True)
class SourceStatus:
    """数据源状态。"""

    requested_source: str
    actual_source: str
    freshness_tier: str
    coverage_tier: str
    health_label: str
    health_message: str
    fallback_used: bool

    @property
    def route(self) -> str:
        """数据源路径（可能包含降级）。"""
        actual = self.actual_source or self.requested_source or "unknown"
        if (
            self.requested_source
            and self.actual_source
            and self.requested_source != self.actual_source
        ):
            return f"{self.requested_source} -> {self.actual_source}"
        return actual

    @property
    def is_degraded(self) -> bool:
        """是否处于降级状态。"""
        return self.health_label in {"fallback", "degraded", "cold_start"}


@dataclass(frozen=True)
class ThemeHeat:
    """题材热度统计。"""

    theme: str
    label: str
    count: int


@dataclass(frozen=True)
class BriefingData:
    """日报的完整结构化数据。

    这是数据流的核心：所有渲染器（markdown/html/email）都从这个结构读取，
    不再反向解析 markdown。
    """

    date: str
    picks: tuple[Pick, ...]
    regime_info: RegimeInfo
    source_status: SourceStatus | None
    research_summary: ResearchSummary | None
    portfolio_summary: PortfolioDecisionSummary | None
    debate_results: tuple[DebateResult, ...] = field(default_factory=tuple)
    theme_heats: tuple[ThemeHeat, ...] = field(default_factory=tuple)

    @property
    def tradable_picks(self) -> tuple[Pick, ...]:
        """可执行的选股（rating 为 tradable）。"""
        from aqsp.ratings import is_tradable_rating

        return tuple(p for p in self.picks if is_tradable_rating(p.rating))

    @property
    def candidate_count(self) -> int:
        """候选标的总数。"""
        return len(self.picks)

    @property
    def actionable_count(self) -> int:
        """纸面复核对象数量。"""
        return len(self.tradable_picks)

    @property
    def top_picks(self) -> tuple[Pick, ...]:
        """前 N 个高分标的（默认 3 个）。"""
        return self.picks[:3]

    @property
    def has_protection(self) -> bool:
        """是否触发组合保护。"""
        return self.regime_info.circuit_breaker_triggered

    @property
    def risk_points(self) -> list[str]:
        """提取所有风险点。"""
        points: list[str] = []
        if self.has_protection:
            reason = self.regime_info.circuit_breaker_reason or "组合保护生效中"
            points.append(f"⚠️ 组合保护已触发: {reason}，暂停新增纸面复核")

        # 从 picks 中提取风险
        for pick in self.picks:
            for risk in pick.risks:
                clean = risk.strip().rstrip("；").strip()
                if clean and len(points) < 3:  # 最多取 3 条
                    points.append(f"⚠️ 风险提示: {clean}")

        return points

    @property
    def debate_points(self) -> list[str]:
        """提取辩论关键结论。"""
        if not self.debate_results:
            return []

        points: list[str] = []
        for result in self.debate_results[:2]:
            if result.recommended_adjustment == "lower":
                points.append(
                    f"🤖 辩论共识: {result.name}({result.symbol}) "
                    f"建议下调评分至{result.adjusted_score:.1f}"
                )
            elif result.recommended_adjustment == "raise":
                points.append(
                    f"🤖 辩论共识: {result.name}({result.symbol}) "
                    f"建议上调评分至{result.adjusted_score:.1f}"
                )
            elif result.disagreement_score > 0.5:
                points.append(
                    f"🤖 辩论分歧较大: {result.name}({result.symbol}) "
                    f"多空分歧度{result.disagreement_score:.0%}"
                )

        return points

    @property
    def source_health_summary(self) -> str | None:
        """数据源健康摘要（仅在降级时返回）。"""
        if self.source_status is None:
            return None
        if not self.source_status.is_degraded:
            return None
        return f"📉 数据源降级: {self.source_status.route}，结果请降低信任度"

    @property
    def regime_summary(self) -> str:
        """市场态势摘要。"""
        desc = self.regime_info.description
        if "熊" in desc or "下跌" in desc:
            return f"📉 市场态势: {desc}，控制纸面暴露"
        if "盘整" in desc:
            return f"📊 市场态势: {desc}，关注突破方向"
        return f"📈 市场态势: {desc}"
