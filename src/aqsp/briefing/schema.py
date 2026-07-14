"""Briefing 数据结构定义 - 结构化数据模型，避免反向解析 markdown。

宪法要求：数据流正向，不反向解析自己生成的 markdown。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from aqsp.briefing.conclusion import (
    build_debate_conclusion_view,
    debate_consensus_point,
)
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
class CommitteeConclusion:
    """委员会结论的可读化投影，不携带原始模型话术。"""

    symbol: str
    name: str
    headline: str
    signal_date: str
    confidence: float | None
    bullish_votes: int
    bearish_votes: int
    neutral_votes: int
    support_points: tuple[str, ...] = field(default_factory=tuple)
    opposition_points: tuple[str, ...] = field(default_factory=tuple)
    risk_points: tuple[str, ...] = field(default_factory=tuple)
    failure_conditions: tuple[str, ...] = field(default_factory=tuple)
    round_count: int = 0
    active_roles: tuple[str, ...] = field(default_factory=tuple)
    llm_advisory_count: int = 0
    advisory_only: bool = True

    @classmethod
    def from_debate_result(cls, result: DebateResult) -> CommitteeConclusion:
        """从辩论结果提取面向读者的结论字段。"""
        view = build_debate_conclusion_view(result)
        opinions = tuple(result.rounds[-1].opinions) if result.rounds else ()
        confidence_values = tuple(
            max(0.0, min(1.0, float(opinion.confidence)))
            for opinion in opinions
            if opinion.confidence is not None
        )
        confidence = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values
            else None
        )
        vote_counts = {
            "bullish": sum(value == "bullish" for value in result.final_vote.values()),
            "bearish": sum(value == "bearish" for value in result.final_vote.values()),
            "neutral": sum(value == "neutral" for value in result.final_vote.values()),
        }
        support_points = _unique_texts(result.support_points)
        opposition_points = _unique_texts(result.opposition_points)
        risk_points = _unique_texts((*result.risk_warnings, result.primary_risk_gate))
        failure_conditions = _failure_conditions(result, view.invalidation_line)
        active_roles = tuple(
            dict.fromkeys(
                opinion.role.value
                for round_data in result.rounds
                for opinion in round_data.opinions
                if opinion.role is not None
            )
        )
        llm_advisory_count = sum(
            bool(opinion.llm_advisory_points)
            for round_data in result.rounds
            for opinion in round_data.opinions
        )
        return cls(
            symbol=result.symbol,
            name=result.name,
            headline=view.headline,
            signal_date=result.related_signal_date,
            confidence=confidence,
            bullish_votes=vote_counts["bullish"],
            bearish_votes=vote_counts["bearish"],
            neutral_votes=vote_counts["neutral"],
            support_points=support_points,
            opposition_points=opposition_points,
            risk_points=risk_points,
            failure_conditions=failure_conditions,
            round_count=len(result.rounds),
            active_roles=active_roles,
            llm_advisory_count=llm_advisory_count,
            advisory_only=bool(result.advisory_only),
        )


@dataclass(frozen=True)
class ArtifactMetadata:
    """可追溯产物元数据。"""

    artifact_id: str
    artifact_type: str
    generated_at: str
    sources: tuple[str, ...] = field(default_factory=tuple)
    input_hash: str = ""
    upstream_versions: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(
        cls,
        *,
        artifact_id: str,
        artifact_type: str,
        generated_at: str,
        payload: Any,
        sources: tuple[str, ...] = (),
        upstream_versions: dict[str, str] | None = None,
    ) -> ArtifactMetadata:
        return cls(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            generated_at=generated_at,
            sources=sources,
            input_hash=_stable_payload_hash(payload),
            upstream_versions=dict(upstream_versions or {}),
        )


@dataclass(frozen=True)
class DecisionContextCard:
    """候选判断上下文卡。"""

    symbol: str
    name: str
    price_signal: str = ""
    news_judgement: str = ""
    cross_market: str = ""
    debate: str = ""
    risk: str = ""
    next_step: str = ""
    artifact_ids: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_pick(cls, pick: Pick) -> DecisionContextCard:
        metrics = pick.metrics or {}
        return cls(
            symbol=pick.symbol,
            name=pick.name,
            price_signal=_first_nonempty((*pick.reasons[:2],)),
            news_judgement=_news_context_line(metrics),
            cross_market=_cross_market_context_line(metrics),
            debate=str(metrics.get("debate_research_verdict", "") or "").strip(),
            risk=_first_nonempty(
                (
                    str(metrics.get("candidate_blocker", "") or ""),
                    *pick.risks[:2],
                )
            ),
            next_step=str(metrics.get("candidate_next_step", "") or "").strip(),
            artifact_ids=_as_text_tuple(metrics.get("artifact_ids")),
        )


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
    artifacts: tuple[ArtifactMetadata, ...] = field(default_factory=tuple)
    decision_context_cards: tuple[DecisionContextCard, ...] = field(
        default_factory=tuple
    )

    def __post_init__(self) -> None:
        if self.decision_context_cards:
            return
        object.__setattr__(
            self,
            "decision_context_cards",
            tuple(DecisionContextCard.from_pick(pick) for pick in self.picks),
        )

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
            point = debate_consensus_point(
                result,
                language="zh-CN",
                max_role_labels=4,
            )
            if point:
                points.append(point)

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


def _stable_payload_hash(payload: Any) -> str:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(payload).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _first_nonempty(values: tuple[str, ...]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _as_text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _unique_texts(values: tuple[object, ...] | list[object]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(str(value).strip() for value in values if str(value).strip())
    )


def _failure_conditions(
    result: DebateResult, invalidation_line: str
) -> tuple[str, ...]:
    pending = _unique_texts(result.pending_confirmations)
    conditions = tuple(
        _strip_failure_prefix(item) for item in pending if "失效" in item
    )
    if conditions:
        return conditions
    if invalidation_line:
        return (_strip_failure_prefix(invalidation_line),)
    if "失效" in result.primary_risk_gate:
        return (_strip_failure_prefix(result.primary_risk_gate),)
    return ()


def _strip_failure_prefix(value: str) -> str:
    text = str(value).strip()
    for prefix in ("失效条件:", "失效信号:"):
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _news_context_line(metrics: dict[str, Any]) -> str:
    judgement = str(metrics.get("news_catalyst_judgement", "") or "").strip()
    if not judgement:
        return ""
    label = {
        "supports": "消息支持",
        "opposes": "消息反对",
        "mixed": "消息分歧",
        "needs_review": "消息待复核",
    }.get(judgement, "消息观察")
    lead = str(metrics.get("news_catalyst_lead", "") or "").strip()
    return f"{label}: {lead}" if lead else label


def _cross_market_context_line(metrics: dict[str, Any]) -> str:
    theme = str(metrics.get("cross_market_primary_theme", "") or "").strip()
    action = str(metrics.get("cross_market_action", "") or "").strip()
    if not theme:
        return ""
    return "｜".join(part for part in (action, theme) if part)
