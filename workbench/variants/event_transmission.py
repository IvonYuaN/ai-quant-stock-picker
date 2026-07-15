"""Experimental event-to-sector transmission model.

This module is intentionally outside ``src/aqsp``. It is a research variant,
not a production scorer or trading signal generator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EventKind = Literal[
    "overseas_theme",
    "global_risk_on",
    "geopolitical_risk",
    "policy_support",
]
Direction = Literal["positive", "negative", "mixed"]


@dataclass(frozen=True)
class MarketEvent:
    """A normalized event used only by the experimental workbench."""

    event_id: str
    headline: str
    source: str
    observed_at: str
    kind: EventKind
    confidence: float
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class SectorHypothesis:
    """A paper-research hypothesis, never a direct recommendation."""

    sector: str
    direction: Direction
    horizon: Literal["intraday", "days", "weeks"]
    confidence: float
    evidence: tuple[str, ...]
    invalidation: str


def _bounded_confidence(value: float) -> float:
    return max(0.0, min(1.0, value))


def infer_sector_hypotheses(event: MarketEvent) -> tuple[SectorHypothesis, ...]:
    """Convert a verified event into ranked sector hypotheses.

    The mapping is deliberately explicit so reviewers can inspect why an
    event produced a hypothesis. The result is not connected to production.
    """

    confidence = _bounded_confidence(event.confidence)
    evidence = (event.source, event.headline)

    if event.kind == "overseas_theme":
        keywords = {keyword.lower() for keyword in event.keywords}
        if {"spacex", "commercial_space", "commercial aerospace"} & keywords:
            return (
                SectorHypothesis(
                    sector="商业航天",
                    direction="positive",
                    horizon="days",
                    confidence=confidence,
                    evidence=evidence,
                    invalidation="海外事件未落地或 A 股映射成交量未扩散",
                ),
            )
        if {"physical_ai", "robotics", "nvidia"} & keywords:
            return (
                SectorHypothesis(
                    sector="机器人与物理AI",
                    direction="positive",
                    horizon="days",
                    confidence=confidence,
                    evidence=evidence,
                    invalidation="主题只停留在叙事，产业链强度和成交未确认",
                ),
                SectorHypothesis(
                    sector="半导体设备与零部件",
                    direction="positive",
                    horizon="weeks",
                    confidence=_bounded_confidence(confidence * 0.85),
                    evidence=evidence,
                    invalidation="海外映射无法对应 A 股订单或业绩",
                ),
            )

    if event.kind == "global_risk_on":
        return (
            SectorHypothesis(
                sector="高贝塔成长",
                direction="positive",
                horizon="intraday",
                confidence=_bounded_confidence(confidence * 0.8),
                evidence=evidence,
                invalidation="A 股开盘不跟随或北向/成交未确认",
            ),
        )

    if event.kind == "geopolitical_risk":
        return (
            SectorHypothesis(
                sector="黄金",
                direction="positive",
                horizon="days",
                confidence=confidence,
                evidence=evidence,
                invalidation="风险事件缓和或避险资金撤出",
            ),
            SectorHypothesis(
                sector="军工",
                direction="positive",
                horizon="days",
                confidence=_bounded_confidence(confidence * 0.9),
                evidence=evidence,
                invalidation="事件没有形成持续订单或政策催化",
            ),
            SectorHypothesis(
                sector="风险资产",
                direction="negative",
                horizon="intraday",
                confidence=_bounded_confidence(confidence * 0.75),
                evidence=evidence,
                invalidation="市场快速定价后风险偏好恢复",
            ),
        )

    if event.kind == "policy_support":
        return (
            SectorHypothesis(
                sector="政策直接受益板块",
                direction="positive",
                horizon="weeks",
                confidence=confidence,
                evidence=evidence,
                invalidation="政策没有预算、订单或增量资金验证",
            ),
        )

    return ()
