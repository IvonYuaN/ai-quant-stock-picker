"""Deterministic quality boundaries for short-term research candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aqsp.strategies.thresholds import ScoringThresholds

QualityAction = Literal["clean", "observe", "blocked"]


@dataclass(frozen=True)
class CandidateQuality:
    """Quality decision independent from portfolio protection and agent advice."""

    action: QualityAction
    paper_review_eligible: bool
    technical_evidence: tuple[str, ...]
    reasons: tuple[str, ...]


def assess_short_term_quality(
    *,
    score: float,
    rating: str,
    ret5_pct: float,
    ret20_pct: float,
    volume_ratio: float,
    rsi12: float,
    bias20_pct: float,
    ma_trend: bool,
    ma_slope_up: bool,
    macd_improving: bool,
    near_high_confirmed: bool,
    pullback_confirmed: bool,
    scoring: ScoringThresholds,
    risk_count: int = 0,
) -> CandidateQuality:
    """Classify a scored symbol for paper review or bounded observation.

    The score remains untouched.  Paper review requires both a tradable rating
    and a recent confirmation; a score alone is never sufficient.  Observation
    is deliberately broader, but still requires two independent technical
    pillars so a weak rank cannot masquerade as a short-term setup.
    """
    evidence: list[str] = []
    if ma_trend and ma_slope_up:
        evidence.append("趋势：均线多头且斜率向上")
    elif ma_trend:
        evidence.append("趋势：均线多头")
    if ret20_pct >= scoring.ret20_strong_threshold or macd_improving:
        evidence.append("动量：20日强势或 MACD 改善")
    if volume_ratio >= scoring.near_high_volume:
        evidence.append("量能：放量确认")
    elif volume_ratio >= scoring.confidence_volume_low:
        evidence.append("量能：至少未明显萎缩")
    if near_high_confirmed or pullback_confirmed:
        evidence.append("结构：突破或趋势回踩")
    if scoring.rsi_healthy_low <= rsi12 <= scoring.rsi_healthy_high:
        evidence.append("强弱：RSI 位于健康区间")

    confirmation_count = sum(
        (
            ret20_pct >= scoring.ret20_strong_threshold or macd_improving,
            volume_ratio >= scoring.confidence_volume_low,
            near_high_confirmed or pullback_confirmed,
        )
    )

    reasons: list[str] = []
    if score < scoring.quality_min_observation_score:
        reasons.append(
            f"评分 {score:.1f} 低于短线观察门槛 "
            f"{scoring.quality_min_observation_score:.1f}"
        )
    if len(evidence) < scoring.quality_min_observation_dimensions:
        reasons.append(
            "独立技术证据不足: "
            f"{len(evidence)}/{scoring.quality_min_observation_dimensions} 个维度"
        )
    if confirmation_count < scoring.quality_min_confirmation_dimensions:
        reasons.append("缺少量价或动能确认")

    # A sharp recent drawdown or a weakening tape is incompatible with a
    # paper-review label, but it may remain visible as an observation.
    recent_weak = ret5_pct <= scoring.quality_max_ret5_drawdown_pct
    overextended = abs(bias20_pct) > scoring.max_bias20
    if recent_weak:
        reasons.append(f"近5日动量偏弱 {ret5_pct:.2f}%")
    if overextended:
        reasons.append(f"偏离20日线过大 {bias20_pct:.2f}%")
    if risk_count > 0:
        reasons.append(f"已有 {risk_count} 项技术风险")

    if (
        score < scoring.quality_min_observation_score
        or len(evidence) < scoring.quality_min_observation_dimensions
        or confirmation_count < scoring.quality_min_confirmation_dimensions
    ):
        return CandidateQuality(
            action="blocked",
            paper_review_eligible=False,
            technical_evidence=tuple(evidence),
            reasons=tuple(dict.fromkeys(reasons)),
        )

    paper_eligible = (
        rating in {"buy_candidate", "strong_buy_candidate"}
        and score >= scoring.quality_min_paper_score
        and len(evidence) >= scoring.quality_min_paper_dimensions
        and not recent_weak
        and not overextended
        and risk_count <= scoring.quality_max_paper_risks
    )
    if paper_eligible:
        return CandidateQuality(
            action="clean",
            paper_review_eligible=True,
            technical_evidence=tuple(evidence),
            reasons=tuple(dict.fromkeys(reasons)),
        )

    if not reasons:
        reasons.append("评分或短线确认不足以进入纸面复核")
    return CandidateQuality(
        action="observe",
        paper_review_eligible=False,
        technical_evidence=tuple(evidence),
        reasons=tuple(dict.fromkeys(reasons)),
    )
