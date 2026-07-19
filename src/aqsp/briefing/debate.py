from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Literal
from uuid import uuid4

import pandas as pd

from aqsp.config import DebateRoleRuntime
from aqsp.briefing.agent_roles import (
    AgentRole,
    DEFAULT_AGENT_ROLE_ORDER,
    agent_role_challenge_style,
    agent_role_description,
    agent_role_focus,
    agent_role_label,
    parse_agent_roles as _parse_agent_roles,
    summarize_context_agent_roles,
    summarize_context_role_plan,
)
from aqsp.core.types import PickResult
from aqsp.utils.llm_safe import llm_call_or_fallback

logger = logging.getLogger(__name__)


class DebateDeadlineExceeded(TimeoutError):
    """整场讨论超过 deadline；不表示 Agent 进程已经失活。"""


def parse_agent_roles(role_names: list[str] | tuple[str, ...]) -> tuple[AgentRole, ...]:
    return _parse_agent_roles(role_names)


@dataclass
class AgentOpinion:
    """单个 Agent 的观点"""

    agent_id: str
    role: AgentRole
    stance: Literal["bullish", "bearish", "neutral"]
    confidence: float  # 0.0-1.0
    arguments: list[str] = field(default_factory=list)
    counterarguments: list[str] = field(default_factory=list)
    counterargument_roles: list[str] = field(default_factory=list)
    # Roles whose concrete opinion was read in this round.  This is separate
    # from counterargument_roles because reviewing a neutral view is still
    # interaction, but is not an opposing vote.
    peer_reviewed_roles: list[str] = field(default_factory=list)
    rebuttal_records: list["RebuttalRecord"] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)  # 风险因素
    opportunity_factors: list[str] = field(default_factory=list)  # 机会因素
    # LLM output is retained for audit only and never replaces rule evidence.
    llm_advisory_points: tuple[str, ...] = field(default_factory=tuple)
    final_position: Literal["bullish", "bearish", "neutral"] | None = None


@dataclass(frozen=True)
class RebuttalRecord:
    """可审计的同行复核或反驳关系。"""

    challenged_role: str
    challenged_claim: str
    rebuttal_reason: str
    challenged_stance: Literal["bullish", "bearish", "neutral"] | str = ""
    opposing_stance: Literal["bullish", "bearish", "neutral"] | str = ""


@dataclass
class DebateRound:
    """辩论的一轮"""

    round_num: int
    opinions: list[AgentOpinion]
    summary: str = ""
    cross_opinions: dict[str, list[str]] = field(default_factory=dict)  # 跨角色观点
    interaction_pairs: tuple[tuple[str, str], ...] = field(default_factory=tuple)


@dataclass
class AgentPerformanceMetrics:
    """单个Agent的历史表现指标（3周窗口）"""

    agent_id: str
    role: AgentRole
    total_predictions: int = 0
    correct_predictions: int = 0
    avg_confidence: float = 0.5
    bias_toward: Literal["bullish", "bearish", "neutral"] = "neutral"

    @property
    def accuracy(self) -> float:
        """准确率"""
        if self.total_predictions == 0:
            return 0.5  # 默认50%
        return self.correct_predictions / self.total_predictions

    @property
    def confidence_calibration(self) -> float:
        """置信度校准：预测准确时置信度是否也高"""
        return self.avg_confidence * self.accuracy

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "role": self.role.value,
            "total_predictions": self.total_predictions,
            "correct_predictions": self.correct_predictions,
            "accuracy": self.accuracy,
            "avg_confidence": self.avg_confidence,
            "bias_toward": self.bias_toward,
        }


@dataclass
class DebateResult:
    """完整辩论结果"""

    debate_id: str
    symbol: str
    name: str
    original_score: float
    rating: str
    rounds: list[DebateRound] = field(default_factory=list)
    debate_rounds_requested: int = 0
    expected_roles: tuple[str, ...] = field(default_factory=tuple)
    data_status: Literal["available", "empty"] = "available"
    data_note: str = ""

    # 溯源信息
    thresholds_version: str = ""
    regime: str = ""
    data_source: str = ""
    related_signal_date: str = ""
    candidate_fingerprint: str = ""
    market_context_lines: tuple[str, ...] = field(default_factory=tuple)

    # 辩论结论
    final_consensus: str = ""
    final_vote: dict[AgentRole, Literal["bullish", "bearish", "neutral"]] = field(
        default_factory=dict
    )

    # 评分调整
    disagreement_score: float = 0.0  # Agent间分歧程度 0~1
    adjustment_weight: float = 0.0  # 调整权重 -1.0~1.0
    adjusted_score: float = 0.0  # 调整后最终评分
    recommended_adjustment: Literal["raise", "lower", "keep"] = "keep"
    adjustment_reason: str = ""
    risk_veto_applied: bool = False
    risk_veto_reason: str = ""

    # 风险与机会
    risk_warnings: list[str] = field(default_factory=list)
    opportunity_highlights: list[str] = field(default_factory=list)
    support_points: tuple[str, ...] = field(default_factory=tuple)
    opposition_points: tuple[str, ...] = field(default_factory=tuple)
    watch_items: tuple[str, ...] = field(default_factory=tuple)
    research_verdict: str = ""
    primary_risk_gate: str = ""
    next_trigger: str = ""
    historical_context_note: str = ""
    historical_context_bucket: str = ""
    historical_context_sample_count: int = 0
    historical_context_accuracy: float = 0.0
    role_reliability_lines: tuple[str, ...] = field(default_factory=tuple)
    role_selection_summary: str = ""
    role_selection_plan: str = ""
    cross_market_support_event_count: int = 0
    cross_market_conflict_event_count: int = 0
    cross_market_evidence_stack_summary: str = ""
    real_message_evidence: tuple[str, ...] = field(default_factory=tuple)
    cross_market_evidence: tuple[str, ...] = field(default_factory=tuple)
    rule_transmission_evidence: tuple[str, ...] = field(default_factory=tuple)
    pending_confirmations: tuple[str, ...] = field(default_factory=tuple)
    falsifiable_conditions: tuple[str, ...] = field(default_factory=tuple)

    # Runtime gates are advisory metadata and never replace the candidate score.
    runtime_status: Literal["paper_review", "observation", "blocked"] = "paper_review"
    realtime_blocked: bool = False
    runtime_blocker: str = ""
    failure: str = ""
    task_id: str = ""
    deadline_seconds: float = 0.0
    deadline_exceeded: bool = False
    heartbeat_count: int = 0
    last_heartbeat_at: str = ""
    deterministic_score: float = 0.0
    deterministic_score_unchanged: bool = True
    advisory_only: bool = True

    # Agent表现快照（辩论时的权重计算依据）
    agent_performance_snapshot: dict[str, AgentPerformanceMetrics] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict:
        """转换为可序列化的字典"""
        # Older callers only populated ``original_score``. Keep the audit
        # field truthful instead of serializing the dataclass default ``0``.
        deterministic_score = (
            self.original_score
            if self.deterministic_score == 0.0 and self.original_score != 0.0
            else self.deterministic_score
        )
        active_roles = tuple(
            dict.fromkeys(
                opinion.role.value
                for round_data in self.rounds
                for opinion in round_data.opinions
                if opinion.role is not None
            )
        )
        quality = _audit_result_quality(self)
        return {
            "debate_id": self.debate_id,
            "symbol": self.symbol,
            "name": self.name,
            "original_score": self.original_score,
            "rating": self.rating,
            "rounds": [
                {
                    "round_num": round_data.round_num,
                    "summary": round_data.summary,
                    "opinions": [
                        {
                            "agent_id": opinion.agent_id,
                            "role": opinion.role.value,
                            "stance": opinion.stance,
                            "confidence": opinion.confidence,
                            "arguments": opinion.arguments,
                            "counterarguments": opinion.counterarguments,
                            "counterargument_roles": opinion.counterargument_roles,
                            "peer_reviewed_roles": opinion.peer_reviewed_roles,
                            "rebuttal_records": [
                                {
                                    "challenged_role": record.challenged_role,
                                    "challenged_claim": record.challenged_claim,
                                    "rebuttal_reason": record.rebuttal_reason,
                                    "challenged_stance": record.challenged_stance,
                                    "opposing_stance": record.opposing_stance,
                                }
                                for record in opinion.rebuttal_records
                            ],
                            "risk_factors": opinion.risk_factors,
                            "opportunity_factors": opinion.opportunity_factors,
                            "llm_advisory_points": list(opinion.llm_advisory_points),
                            "final_position": opinion.final_position,
                        }
                        for opinion in round_data.opinions
                    ],
                    "cross_opinions": round_data.cross_opinions,
                    "interaction_pairs": [
                        list(pair) for pair in round_data.interaction_pairs
                    ],
                }
                for round_data in self.rounds
            ],
            "thresholds_version": self.thresholds_version,
            "regime": self.regime,
            "data_source": self.data_source,
            "related_signal_date": self.related_signal_date,
            "expected_roles": list(self.expected_roles),
            "data_status": self.data_status,
            "data_note": self.data_note,
            "candidate_fingerprint": self.candidate_fingerprint,
            "market_context_lines": list(self.market_context_lines),
            "disagreement_score": self.disagreement_score,
            "adjustment_weight": self.adjustment_weight,
            "adjusted_score": self.adjusted_score,
            "recommended_adjustment": self.recommended_adjustment,
            "adjustment_reason": self.adjustment_reason,
            "risk_veto_applied": self.risk_veto_applied,
            "risk_veto_reason": self.risk_veto_reason,
            "final_consensus": self.final_consensus,
            "final_vote": {k.value: v for k, v in self.final_vote.items()},
            "risk_warnings": self.risk_warnings,
            "opportunity_highlights": self.opportunity_highlights,
            "support_points": list(self.support_points),
            "opposition_points": list(self.opposition_points),
            "watch_items": list(self.watch_items),
            "research_verdict": self.research_verdict,
            "primary_risk_gate": self.primary_risk_gate,
            "next_trigger": self.next_trigger,
            "historical_context_note": self.historical_context_note,
            "historical_context_bucket": self.historical_context_bucket,
            "historical_context_sample_count": self.historical_context_sample_count,
            "historical_context_accuracy": self.historical_context_accuracy,
            "role_reliability_lines": list(self.role_reliability_lines),
            "role_selection_summary": self.role_selection_summary,
            "role_selection_plan": self.role_selection_plan,
            "cross_market_support_event_count": self.cross_market_support_event_count,
            "cross_market_conflict_event_count": self.cross_market_conflict_event_count,
            "cross_market_evidence_stack_summary": self.cross_market_evidence_stack_summary,
            "real_message_evidence": list(self.real_message_evidence),
            "cross_market_evidence": list(self.cross_market_evidence),
            "rule_transmission_evidence": list(self.rule_transmission_evidence),
            "pending_confirmations": list(self.pending_confirmations),
            "falsifiable_conditions": list(self.falsifiable_conditions),
            "runtime_status": self.runtime_status,
            "realtime_blocked": self.realtime_blocked,
            "runtime_blocker": self.runtime_blocker,
            "failure": self.failure
            or ("；".join(quality.issues) if not quality.passed else ""),
            "deterministic_score": deterministic_score,
            "deterministic_score_unchanged": self.deterministic_score_unchanged,
            "advisory_only": self.advisory_only,
            "task_id": self.task_id,
            "deadline_seconds": self.deadline_seconds,
            "deadline_exceeded": self.deadline_exceeded,
            "heartbeat_count": self.heartbeat_count,
            "last_heartbeat_at": self.last_heartbeat_at,
            "agent_performance_snapshot": {
                k: v.to_dict() for k, v in self.agent_performance_snapshot.items()
            },
            # Persist process metadata alongside the conclusion. Consumers
            # must not infer completeness from rendered markdown.
            "active_roles": list(active_roles),
            "debate_rounds_requested": self.debate_rounds_requested or len(self.rounds),
            "debate_rounds_completed": len(self.rounds),
            "process_recorded": quality.process_recorded,
            "conclusion_recorded": quality.conclusion_recorded,
            "debate_quality_issues": list(quality.issues),
            "evidence_sufficient": quality.evidence_sufficient,
            "advisory_boundary_ok": quality.advisory_boundary_ok,
            "discussion_agent_count": quality.discussion_agent_count,
            "stance_counts": dict(quality.stance_counts),
            "rebuttal_count": quality.rebuttal_count,
            "real_opposition_count": quality.real_opposition_count,
            "message_evidence_recorded": quality.message_evidence_recorded,
            "transmission_evidence_recorded": quality.transmission_evidence_recorded,
            "viewpoint_coverage": {
                "support": quality.support_recorded,
                "opposition": quality.opposition_recorded,
                "risk": quality.risk_recorded,
                "cross_market": quality.cross_market_recorded,
            },
            "advisory_adjusted_score": self.adjusted_score,
            "adjusted_score_is_advisory": self.advisory_only,
            "score_boundary": {
                "deterministic_score": deterministic_score,
                "original_score": self.original_score,
                "unchanged": self.deterministic_score_unchanged,
                "advisory_only": self.advisory_only,
            },
        }


def _is_st_risk_pick(pick: PickResult) -> bool:
    name = str(pick.name or "").strip().upper()
    if name.startswith(("ST", "*ST")) or "退市" in name:
        return True
    risk_text = "；".join(str(item) for item in pick.risks).upper()
    return any(marker in risk_text for marker in ("ST股", "*ST", "退市风险"))


def _pick_risk_items(pick: PickResult) -> tuple[str, ...]:
    """Return explicit candidate risks, excluding empty placeholders."""
    return tuple(
        dict.fromkeys(
            text for item in (pick.risks or ()) if (text := str(item).strip())
        )
    )


class AShareDebateAgent:
    """A股市场辩论 Agent 基类"""

    def __init__(
        self,
        role: AgentRole,
        enable_llm: bool = False,
        language: str = "zh-CN",
        llm_provider: str = "",
        llm_model: str = "",
    ):
        self.role = role
        self.enable_llm = enable_llm
        self.language = language
        self.llm_provider = llm_provider.strip().lower()
        self.llm_model = llm_model.strip()
        self.agent_id = self._build_stable_agent_id()

    def get_role_description(self) -> str:
        """获取角色描述"""
        return agent_role_description(self.role, self.language)

    def _build_stable_agent_id(self) -> str:
        mode = "llm" if self.enable_llm else "rule"
        provider = self.llm_provider or "local"
        model = self.llm_model or "default"
        signature = "|".join(
            (
                self.role.value,
                mode,
                self.language.strip().lower() or "zh-cn",
                provider,
                model,
            )
        )
        digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]
        return f"{self.role.value}_{digest}"

    def generate_initial_opinion(
        self,
        pick: PickResult,
        df: pd.DataFrame,
        market_context_lines: tuple[str, ...] = (),
    ) -> AgentOpinion:
        """生成初始观点"""
        stance = self._determine_stance(pick, market_context_lines)
        confidence = self._calculate_confidence(pick, df)
        arguments = self._build_arguments(pick, df, stance, market_context_lines)
        risk_factors = self._identify_risk_factors(pick, df, market_context_lines)
        opportunity_factors = self._identify_opportunity_factors(
            pick, df, market_context_lines
        )
        risk_factors = self._ensure_falsifiable_counterclaim(
            pick,
            risk_factors,
            market_context_lines=market_context_lines,
        )

        opinion = AgentOpinion(
            agent_id=self.agent_id,
            role=self.role,
            stance=stance,
            confidence=confidence,
            arguments=arguments,
            risk_factors=risk_factors,
            opportunity_factors=opportunity_factors,
        )
        return self._maybe_enhance_initial_opinion(
            opinion,
            pick,
            market_context_lines=market_context_lines,
        )

    def _ensure_falsifiable_counterclaim(
        self,
        pick: PickResult,
        risk_factors: list[str],
        *,
        market_context_lines: tuple[str, ...] = (),
    ) -> list[str]:
        """Add a conditional challenge when support evidence is present.

        This is a research challenge, not a vote.  In particular, a neutral
        risk reviewer remains neutral; the quality gate must not manufacture a
        bearish stance just to make the discussion look balanced.
        """
        if self.role == AgentRole.BULL or not _has_supporting_evidence(
            pick,
            market_context_lines,
        ):
            return risk_factors
        if any(_contains_falsifiable_marker(item) for item in risk_factors):
            return risk_factors

        if self.role == AgentRole.CROSS_MARKET or any(
            "跨市" in str(line) or "传导" in str(line) for line in market_context_lines
        ):
            challenge = (
                "⚠️ 反方可证伪主张: 跨市支持线索只有在A股板块共振并出现量价承接时成立，"
                "若未出现板块扩散或承接转弱，则该主张失效"
            )
        else:
            challenge = (
                "⚠️ 反方可证伪主张: 支持证据只有在盘中量价承接延续时成立，"
                "若冲高回落或量价背离，则该主张失效"
            )
        return [*risk_factors, challenge][:3]

    def _maybe_enhance_initial_opinion(
        self,
        opinion: AgentOpinion,
        pick: PickResult,
        *,
        market_context_lines: tuple[str, ...] = (),
    ) -> AgentOpinion:
        if not self.enable_llm:
            return opinion

        fallback_payload = {
            "arguments": opinion.arguments[:2],
            "risk_factors": opinion.risk_factors[:2],
            "opportunity_factors": opinion.opportunity_factors[:2],
        }
        old_provider = os.getenv("LLM_PROVIDER")
        try:
            if self.llm_provider:
                os.environ["LLM_PROVIDER"] = self.llm_provider
            result = llm_call_or_fallback(
                prompt=self._build_initial_prompt(
                    pick,
                    opinion,
                    market_context_lines=market_context_lines,
                ),
                fallback=json.dumps(fallback_payload, ensure_ascii=False),
                enable_llm=self.enable_llm,
                model=self.llm_model or None,
                caller=f"debate-initial-{self.role.value}",
            )
        finally:
            if old_provider is None:
                os.environ.pop("LLM_PROVIDER", None)
            else:
                os.environ["LLM_PROVIDER"] = old_provider
        payload = self._parse_llm_payload(result.text, fallback_payload)
        llm_advisory_points: list[str] = []
        for label, key in (
            ("论点", "arguments"),
            ("风险", "risk_factors"),
            ("机会", "opportunity_factors"),
        ):
            for point in self._normalize_points(payload.get(key), []):
                llm_advisory_points.append(f"{label}: {point}")
        return AgentOpinion(
            agent_id=opinion.agent_id,
            role=opinion.role,
            stance=opinion.stance,
            confidence=opinion.confidence,
            arguments=opinion.arguments.copy(),
            counterarguments=opinion.counterarguments.copy(),
            counterargument_roles=opinion.counterargument_roles.copy(),
            peer_reviewed_roles=opinion.peer_reviewed_roles.copy(),
            rebuttal_records=opinion.rebuttal_records.copy(),
            risk_factors=opinion.risk_factors.copy(),
            opportunity_factors=opinion.opportunity_factors.copy(),
            llm_advisory_points=tuple(llm_advisory_points),
            final_position=opinion.final_position,
        )

    def _build_initial_prompt(
        self,
        pick: PickResult,
        opinion: AgentOpinion,
        *,
        market_context_lines: tuple[str, ...] = (),
    ) -> str:
        market_context = (
            "；".join(
                line.strip() for line in market_context_lines if str(line).strip()
            )
            or "无"
        )
        return f"""
你是 A 股多 Agent 委员会中的一个固定角色。

角色: {agent_role_label(self.role, self.language)}
角色描述: {agent_role_description(self.role, self.language)}
观察焦点: {agent_role_focus(self.role, self.language)}
反驳风格: {agent_role_challenge_style(self.role, self.language)}

硬约束:
1. 不允许改变立场，只能围绕既定立场补充更有辨识度的论点。
2. 不允许输出泛泛而谈的话。
3. 不允许捏造不存在的数据。
4. 只输出 JSON，对应字段最多 2/2/2 条短句。

标的:
- symbol: {pick.symbol}
- name: {pick.name}
- score: {pick.score}
- rating: {pick.rating}
- stance: {opinion.stance}
- reasons: {"；".join(pick.reasons) or "无"}
- risks: {"；".join(pick.risks) or "无"}
- strategies: {",".join(pick.strategies) or "无"}
- market_context: {market_context}

已有基础观点:
- arguments: {"；".join(opinion.arguments) or "无"}
- risk_factors: {"；".join(opinion.risk_factors) or "无"}
- opportunity_factors: {"；".join(opinion.opportunity_factors) or "无"}

输出格式:
{{
  "arguments": ["..."],
  "risk_factors": ["..."],
  "opportunity_factors": ["..."]
}}
""".strip()

    @staticmethod
    def _parse_llm_payload(
        text: str,
        fallback_payload: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return fallback_payload
        if not isinstance(payload, dict):
            return fallback_payload
        return {
            "arguments": payload.get("arguments", fallback_payload["arguments"]),
            "risk_factors": payload.get(
                "risk_factors", fallback_payload["risk_factors"]
            ),
            "opportunity_factors": payload.get(
                "opportunity_factors",
                fallback_payload["opportunity_factors"],
            ),
        }

    @staticmethod
    def _normalize_points(
        values: object,
        fallback: list[str],
        *,
        limit: int = 2,
    ) -> list[str]:
        if not isinstance(values, list):
            values = fallback
        cleaned: list[str] = []
        for raw in values:
            text = str(raw).strip()
            if text and text not in cleaned:
                cleaned.append(text)
            if len(cleaned) >= limit:
                break
        return cleaned or fallback[:limit]

    def _determine_stance(
        self,
        pick: PickResult,
        market_context_lines: tuple[str, ...] = (),
    ) -> Literal["bullish", "bearish", "neutral"]:
        """确定立场"""
        validation_signals = self._cross_market_metric_texts(
            pick,
            "cross_market_validation_signals",
        )
        invalidation_signals = self._cross_market_metric_texts(
            pick,
            "cross_market_invalidation_signals",
        )
        pressure_targets = self._cross_market_metric_texts(
            pick,
            "cross_market_pressure_targets",
        )
        support_count, conflict_count = self._cross_market_evidence_counts(
            pick,
            market_context_lines=market_context_lines,
        )
        if self.role == AgentRole.BULL:
            if (
                validation_signals
                and support_count >= conflict_count
                and pick.score >= 45
            ):
                return "bullish"
            return "bullish" if pick.score > 50 else "neutral"
        elif self.role == AgentRole.BEAR:
            if (
                invalidation_signals
                or pressure_targets
                or _pick_risk_items(pick)
                or conflict_count > 0
                or any(
                    marker in str(line)
                    for marker in ("动作 观察为主", "承压方向:", "失效条件:")
                    for line in market_context_lines
                )
            ):
                return "bearish"
            return "bearish" if pick.score < 50 else "neutral"
        elif self.role == AgentRole.RISK_CONTROL:
            # 风控更保守
            if _is_st_risk_pick(pick) or _pick_risk_items(pick):
                return "bearish"
            if invalidation_signals or pressure_targets or conflict_count > 0:
                return "bearish"
            return "bearish" if pick.score < 60 else "neutral"
        elif self.role == AgentRole.SECTOR_LEADER:
            # 板块证据既可以支持扩散，也可以否定单点题材。
            if pressure_targets or invalidation_signals:
                return "bearish"
            if conflict_count > support_count and conflict_count > 0:
                return "bearish"
            if (
                self._has_cross_market_evidence(
                    pick,
                    market_context_lines=market_context_lines,
                )
                and (
                    self._cross_market_metric_texts(
                        pick, "cross_market_first_order_targets"
                    )
                    or self._cross_market_metric_texts(
                        pick, "cross_market_second_order_targets"
                    )
                )
                and support_count > conflict_count
            ):
                return "bullish"
            return "neutral"
        elif self.role == AgentRole.CROSS_MARKET:
            if not self._has_cross_market_evidence(
                pick,
                market_context_lines=market_context_lines,
            ):
                return "neutral"
            action = str(pick.metrics.get("cross_market_action", "") or "").strip()
            priority_score = int(
                pick.metrics.get("cross_market_priority_score", 0) or 0
            )
            if conflict_count > support_count and conflict_count > 0:
                return "bearish"
            if action == "优先复核" or priority_score >= 3:
                if conflict_count >= support_count and conflict_count > 0:
                    return "neutral"
                return "bullish"
            if market_context_lines and (
                action == "观察为主"
                or any("动作 观察为主" in str(line) for line in market_context_lines)
            ):
                return "bearish"
            if support_count >= 2 and conflict_count == 0:
                return "bullish"
            if conflict_count > 0 and support_count <= conflict_count:
                return "bearish"
            if action == "重点跟踪" or priority_score >= 2:
                return "bullish"
            return "neutral"
        elif self.role == AgentRole.POLICY_SENSITIVE:
            return (
                self._context_stance(
                    market_context_lines,
                    selectors=("政策", "监管", "发改委", "工信部", "财政部"),
                    bullish_markers=("支持", "推进", "落地", "加码", "鼓励", "扩围"),
                    bearish_markers=("收紧", "处罚", "限制", "叫停", "压降", "风险"),
                )
                or "neutral"
            )
        elif self.role == AgentRole.MARGIN_TRADING:
            return (
                self._context_stance(
                    market_context_lines,
                    selectors=("融资", "杠杆", "融券"),
                    bullish_markers=("净买入", "余额上升", "偏强", "改善", "增加"),
                    bearish_markers=(
                        "净偿还",
                        "余额下降",
                        "偏弱",
                        "拥挤",
                        "踩踏",
                        "回落",
                    ),
                )
                or "neutral"
            )
        elif self.role == AgentRole.NORTHBOUND:
            return (
                self._context_stance(
                    market_context_lines,
                    selectors=("北向资金", "外资", "陆股通"),
                    bullish_markers=("净流入", "偏强", "改善", "增配", "风险偏好上升"),
                    bearish_markers=("净流出", "偏弱", "减配", "风险偏好回落", "回落"),
                )
                or "neutral"
            )
        elif self.role == AgentRole.RETAIL_MOOD:
            return (
                self._context_stance(
                    market_context_lines,
                    selectors=("全局雷达", "市场情绪", "散户", "拥挤度"),
                    bullish_markers=(
                        "偏多",
                        "偏强",
                        "风险偏好改善",
                        "情绪修复",
                        "扩散",
                    ),
                    bearish_markers=(
                        "偏空",
                        "偏弱",
                        "风险偏好回落",
                        "情绪退潮",
                        "拥挤",
                    ),
                )
                or "neutral"
            )
        return "neutral"

    @staticmethod
    def _context_stance(
        market_context_lines: tuple[str, ...],
        *,
        selectors: tuple[str, ...],
        bullish_markers: tuple[str, ...],
        bearish_markers: tuple[str, ...],
    ) -> Literal["bullish", "bearish"] | None:
        """从角色相关上下文提取方向；无证据或多空同分时保持中性。"""
        bullish = 0
        bearish = 0
        for raw in market_context_lines:
            line = str(raw).strip()
            if not line or not any(selector in line for selector in selectors):
                continue
            bullish += sum(marker in line for marker in bullish_markers)
            bearish += sum(marker in line for marker in bearish_markers)
        if bullish > bearish:
            return "bullish"
        if bearish > bullish:
            return "bearish"
        return None

    def _calculate_confidence(
        self,
        pick: PickResult,
        df: pd.DataFrame,
    ) -> float:
        """计算信心指数"""
        base_confidence = 0.5

        if self.role == AgentRole.BULL:
            if pick.score > 70:
                base_confidence = 0.8
            elif pick.score > 60:
                base_confidence = 0.7
            elif pick.score > 50:
                base_confidence = 0.6
        elif self.role == AgentRole.BEAR:
            if pick.score < 30:
                base_confidence = 0.8
            elif pick.score < 40:
                base_confidence = 0.7
            elif pick.score < 50:
                base_confidence = 0.6
        elif self.role == AgentRole.RISK_CONTROL:
            # 风控在低分时更有信心
            base_confidence = 0.7

        # 根据数据质量调整
        if not df.empty and len(df) >= 20:
            base_confidence += 0.1

        return min(1.0, max(0.3, base_confidence))

    def _build_arguments(
        self,
        pick: PickResult,
        df: pd.DataFrame,
        stance: str,
        market_context_lines: tuple[str, ...] = (),
    ) -> list[str]:
        """构建论据"""
        args = []
        validation_signals = self._cross_market_metric_texts(
            pick,
            "cross_market_validation_signals",
        )
        invalidation_signals = self._cross_market_metric_texts(
            pick,
            "cross_market_invalidation_signals",
        )
        pressure_targets = self._cross_market_metric_texts(
            pick,
            "cross_market_pressure_targets",
        )
        first_order_targets = self._cross_market_metric_texts(
            pick,
            "cross_market_first_order_targets",
        )
        second_order_targets = self._cross_market_metric_texts(
            pick,
            "cross_market_second_order_targets",
        )
        support_count, conflict_count = self._cross_market_evidence_counts(
            pick,
            market_context_lines=market_context_lines,
        )

        if self.role == AgentRole.BULL:
            if stance == "bullish":
                if pick.reasons:
                    args.append(f"确定性评分理由: {pick.reasons[0]}")
                else:
                    args.append("输入未提供额外技术理由，不能确认趋势结构")
                if not df.empty and "close" in df.columns and len(df) >= 2:
                    latest = float(df.iloc[-1]["close"])
                    previous = float(df.iloc[-2]["close"])
                    direction = "上升" if latest >= previous else "回落"
                    args.append(f"最近一根收盘价较前一根{direction}")
                if validation_signals:
                    args.append(f"先确认 {validation_signals[0]}")
                if support_count >= 2:
                    args.append(f"已记录同向证据 {support_count} 条")
            else:
                args.append("当前输入不足以确认技术面延续")
        elif self.role == AgentRole.BEAR:
            if stance == "bearish":
                if pick.score < 40:
                    args.append("确定性评分偏低，短线强度不足")
                elif pick.risks:
                    args.append(f"已记录风险: {pick.risks[0]}")
                else:
                    args.append("输入未提供估值或业绩反向证据，不能据此扩大看空结论")
                if invalidation_signals:
                    args.append(f"失效条件已明确: {invalidation_signals[0]}")
                if pressure_targets:
                    args.append(f"当前承压方向包括 {'、'.join(pressure_targets[:2])}")
                if conflict_count > 0:
                    args.append(f"已记录反向证据 {conflict_count} 条")
            else:
                args.append("输入未提供足够反向证据，暂不扩大看空结论")
        elif self.role == AgentRole.RISK_CONTROL:
            args.append(self._analyze_risk_factors(pick))
            if invalidation_signals:
                args.append(f"一旦出现 {invalidation_signals[0]}，应取消纸面复核")
            if pressure_targets:
                args.append(
                    f"若 {'、'.join(pressure_targets[:2])} 持续承压，需防止风格切换失败"
                )
        elif self.role == AgentRole.SECTOR_LEADER:
            args.append("输入未提供板块共振证据，无法确认热点持续性")
            if first_order_targets:
                args.append(f"先看 {'、'.join(first_order_targets[:3])} 是否同步走强")
            if second_order_targets:
                args.append(
                    f"若扩散到 {'、'.join(second_order_targets[:2])}，板块持续性更强"
                )
            if pressure_targets:
                args.append(f"同时观察 {'、'.join(pressure_targets[:2])} 是否承压让位")
        elif self.role == AgentRole.CROSS_MARKET:
            theme = str(
                pick.metrics.get("cross_market_primary_theme", "") or ""
            ).strip()
            basis = str(
                pick.metrics.get("cross_market_linkage_basis", "") or ""
            ).strip()
            action = str(pick.metrics.get("cross_market_action", "") or "").strip()
            source_quality_label, source_quality_score = (
                self._cross_market_source_quality(
                    pick,
                    market_context_lines=market_context_lines,
                )
            )
            lead_window = str(
                pick.metrics.get("cross_market_lead_window", "") or ""
            ).strip()
            window = str(
                pick.metrics.get("cross_market_observation_window", "") or ""
            ).strip()
            execution_watchpoints = tuple(
                str(item).strip()
                for item in (
                    pick.metrics.get("cross_market_execution_watchpoints") or ()
                )
                if str(item).strip()
            )
            evidence_stack_summary = self._cross_market_evidence_stack_summary(
                pick,
                market_context_lines=market_context_lines,
            )
            has_cross_market_evidence = self._has_cross_market_evidence(
                pick,
                market_context_lines=market_context_lines,
            )
            if not has_cross_market_evidence:
                args.append("无可用跨市消息或规则传导，不据此形成判断")
                # Theme/action/targets are hypotheses or routing metadata. They
                # must not be rendered as sourced evidence when the evidence
                # gate above is closed.
                theme = ""
                basis = ""
                action = ""
                lead_window = ""
                window = ""
                source_quality_label = ""
                source_quality_score = 0
                execution_watchpoints = ()
                evidence_stack_summary = ""
                first_order_targets = ()
                second_order_targets = ()
                pressure_targets = ()
                validation_signals = ()
                support_count = 0
                conflict_count = 0
            if theme:
                args.append(f"海外主线已映射到A股方向: {theme}")
            if basis and lead_window:
                args.append(f"传导类型 {basis}，领先窗 {lead_window}")
            elif basis:
                args.append(f"当前按{basis}逻辑处理跨市映射")
            if action and window:
                args.append(f"传导动作 {action}，观察窗 {window}")
            elif action:
                args.append(f"当前更适合按{action}节奏处理")
            if source_quality_label:
                if source_quality_score >= 3:
                    args.append(f"来源质量 {source_quality_label}，跨市主线可信度更高")
                else:
                    args.append(f"来源质量 {source_quality_label}，仍需A股盘中映射确认")
            if first_order_targets:
                args.append(f"先看A股先手链条: {'、'.join(first_order_targets[:3])}")
            if second_order_targets:
                args.append(
                    f"若扩散到 {'、'.join(second_order_targets[:2])}，持续性更强"
                )
            if pressure_targets:
                args.append(f"同时留意承压方向: {'、'.join(pressure_targets[:2])}")
            if execution_watchpoints:
                args.append(f"盘中先盯 {execution_watchpoints[0]}")
            if evidence_stack_summary:
                args.append(f"跨市证据堆栈: {evidence_stack_summary}")
            if support_count >= 2:
                args.append(f"同向证据 {support_count} 条，海外主题不是单点脉冲。")
            if conflict_count > 0:
                args.append(
                    f"但仍有反向证据 {conflict_count} 条，需确认A股映射能否延续。"
                )
            if validation_signals:
                args.append(f"先看 {validation_signals[0]}")
            # A missing theme is not evidence of an overseas narrative.  Do not
            # manufacture a cross-market claim from unrelated HMM/news status lines.
            if not theme and has_cross_market_evidence and market_context_lines:
                args.append("跨市场线索存在，但仍需确认是否形成A股主线接力")
        elif self.role == AgentRole.POLICY_SENSITIVE:
            args.append("输入未提供可核验的政策或监管证据")
        elif self.role == AgentRole.MARGIN_TRADING:
            args.append("输入未提供融资余额或杠杆拥挤数据")
        elif self.role == AgentRole.NORTHBOUND:
            args.append("输入未提供北向或外资流向数据")
        elif self.role == AgentRole.RETAIL_MOOD:
            args.append("输入未提供散户情绪或拥挤度数据")

        args.extend(
            self._context_points_for_role(
                market_context_lines,
                category="arguments",
            )
        )
        return args

    def _analyze_risk_factors(self, pick: PickResult) -> str:
        """分析风险因素"""
        risks = []

        risks.extend(f"候选明确风险: {risk}" for risk in _pick_risk_items(pick))

        if _is_st_risk_pick(pick):
            risks.append("ST股风险")
        if "涨停" in str(pick.risks) or "涨停" in str(pick.reasons):
            risks.append("涨停板流动性风险")
        if pick.score > 80:
            risks.append("高分股回调风险")

        if risks:
            return f"风险提示: {', '.join(risks)}"
        return "风险检验: 高分延续需盘中承接，若冲高回落或量价背离则降级"

    def _identify_risk_factors(
        self,
        pick: PickResult,
        df: pd.DataFrame,
        market_context_lines: tuple[str, ...] = (),
    ) -> list[str]:
        """识别风险因素"""
        risks: list[str] = []
        generic_risks = [f"策略提示: {risk}" for risk in _pick_risk_items(pick)]
        invalidation_signals = self._cross_market_metric_texts(
            pick,
            "cross_market_invalidation_signals",
        )
        pressure_targets = self._cross_market_metric_texts(
            pick,
            "cross_market_pressure_targets",
        )

        if self.role == AgentRole.RISK_CONTROL:
            if _is_st_risk_pick(pick):
                risks.append("⚠️ ST股：存在退市风险")
            if pick.score > 80:
                risks.append("⚠️ 高分股：警惕回调风险")
            if not df.empty and len(df) >= 5:
                latest = df.iloc[-1]
                prev = df.iloc[-2]
                if latest["close"] > prev["close"] * 1.09:
                    risks.append("⚠️ 接近涨停：流动性风险")
            if invalidation_signals:
                risks.append(f"⚠️ 失效先看: {invalidation_signals[0]}")
            if pressure_targets:
                risks.append(f"⚠️ 承压方向: {'、'.join(pressure_targets[:2])}")
            if not risks:
                risks.append(
                    "⚠️ 失效检验: 高分延续需盘中承接，若冲高回落或量价背离则降级"
                )
        elif self.role == AgentRole.BEAR:
            if pick.score < 50:
                risks.append("⚠️ 技术面偏弱")
            if pick.score < 40:
                risks.append("⚠️ 基本面支撑不足")
            if invalidation_signals:
                risks.append(f"⚠️ 失效条件: {invalidation_signals[0]}")
            if pressure_targets:
                risks.append(f"⚠️ 承压方向: {'、'.join(pressure_targets[:2])}")
            if pick.score >= 50 and not risks:
                risks.append("⚠️ 反方检验: 高分不等于延续，若放量承接不足则看空假设成立")
        elif self.role == AgentRole.SECTOR_LEADER:
            risks.append("⚠️ 板块轮动可能导致风格切换")
            if pressure_targets:
                risks.append(
                    f"⚠️ 若 {'、'.join(pressure_targets[:2])} 持续承压，轮动可能失败"
                )
        elif self.role == AgentRole.CROSS_MARKET:
            action = str(pick.metrics.get("cross_market_action", "") or "").strip()
            has_cross_market_evidence = self._has_cross_market_evidence(
                pick,
                market_context_lines=market_context_lines,
            )
            source_quality_label, source_quality_score = (
                self._cross_market_source_quality(
                    pick,
                    market_context_lines=market_context_lines,
                )
            )
            _, conflict_count = self._cross_market_evidence_counts(
                pick,
                market_context_lines=market_context_lines,
            )
            if not has_cross_market_evidence:
                risks.append("⚠️ 无可用消息或规则传导证据，跨市角色不形成结论")
            elif action == "观察为主":
                risks.append("⚠️ 跨市传导证据偏弱，先防范题材联想先行")
            else:
                risks.append("⚠️ 海外叙事未必立刻传到A股，需确认板块共振")
            if source_quality_label and source_quality_score <= 2:
                risks.append(
                    f"⚠️ 来源质量仅 {source_quality_label}，避免单条消息抬升优先级"
                )
            if conflict_count > 0:
                risks.append(f"⚠️ 反向证据 {conflict_count} 条，跨市强化链条已出现分歧")
            if invalidation_signals:
                risks.append(f"⚠️ 失效条件: {invalidation_signals[0]}")
        elif self.role == AgentRole.POLICY_SENSITIVE:
            risks.append("未提供可核验的政策或监管风险证据")
        elif self.role == AgentRole.MARGIN_TRADING:
            risks.append("未提供融资余额或杠杆拥挤数据")
        elif self.role == AgentRole.NORTHBOUND:
            risks.append("未提供北向或外资流向数据")
        elif self.role == AgentRole.RETAIL_MOOD:
            risks.append("未提供散户情绪或拥挤度数据")

        risks.extend(
            self._context_points_for_role(
                market_context_lines,
                category="risks",
            )
        )
        risks.extend(generic_risks)
        return risks[:3]  # 最多返回3个风险因素

    def _identify_opportunity_factors(
        self,
        pick: PickResult,
        df: pd.DataFrame,
        market_context_lines: tuple[str, ...] = (),
    ) -> list[str]:
        """识别机会因素"""
        opportunities: list[str] = []
        generic_opportunities: list[str] = []
        if pick.strategies:
            generic_opportunities.append(f"命中策略: {', '.join(pick.strategies)}")
        validation_signals = self._cross_market_metric_texts(
            pick,
            "cross_market_validation_signals",
        )
        pressure_targets = self._cross_market_metric_texts(
            pick,
            "cross_market_pressure_targets",
        )
        first_order_targets = self._cross_market_metric_texts(
            pick,
            "cross_market_first_order_targets",
        )
        second_order_targets = self._cross_market_metric_texts(
            pick,
            "cross_market_second_order_targets",
        )
        support_count, _ = self._cross_market_evidence_counts(
            pick,
            market_context_lines=market_context_lines,
        )

        if self.role == AgentRole.BULL:
            if pick.score > 60:
                opportunities.append(f"确定性评分为 {pick.score:.1f}")
                if "突破" in str(pick.reasons):
                    opportunities.append("候选理由包含突破信号")
            if not df.empty and len(df) >= 10:
                recent_trend = df.tail(10)["close"].values
                if all(
                    recent_trend[i] <= recent_trend[i + 1]
                    for i in range(len(recent_trend) - 1)
                ):
                    opportunities.append("✅ 10日连续上涨趋势")
            if validation_signals:
                opportunities.append(f"✅ 验证重点: {validation_signals[0]}")
        elif self.role == AgentRole.SECTOR_LEADER:
            if first_order_targets or second_order_targets or pressure_targets:
                if first_order_targets:
                    opportunities.append(
                        f"✅ 先手链条: {'、'.join(first_order_targets[:3])}"
                    )
                if second_order_targets:
                    opportunities.append(
                        f"✅ 扩散看点: {'、'.join(second_order_targets[:2])}"
                    )
                if pressure_targets:
                    opportunities.append(f"✅ 轮动观察: {pressure_targets[0]}")
            else:
                opportunities.append("未提供板块扩散证据，暂不确认轮动机会")
        elif self.role == AgentRole.CROSS_MARKET:
            theme = str(
                pick.metrics.get("cross_market_primary_theme", "") or ""
            ).strip()
            action = str(pick.metrics.get("cross_market_action", "") or "").strip()
            source_quality_label, source_quality_score = (
                self._cross_market_source_quality(
                    pick,
                    market_context_lines=market_context_lines,
                )
            )
            has_cross_market_evidence = self._has_cross_market_evidence(
                pick,
                market_context_lines=market_context_lines,
            )
            if not has_cross_market_evidence:
                # Do not turn a theme, action, or watch condition into an
                # opportunity without a sourced event or evidence count.
                theme = ""
                action = ""
                source_quality_label = ""
                source_quality_score = 0
                validation_signals = ()
                pressure_targets = ()
                support_count = 0
            if source_quality_label and source_quality_score >= 3:
                opportunities.append(f"✅ 来源质量较高: {source_quality_label}")
            if support_count >= 2:
                opportunities.append(
                    f"✅ 同向证据 {support_count} 条，海外主题连续强化"
                )
            if validation_signals:
                opportunities.append(f"✅ 验证重点: {validation_signals[0]}")
            if theme:
                opportunities.append(f"✅ 跨市传导匹配: {theme}")
            if action in {"优先复核", "重点跟踪"}:
                opportunities.append(f"✅ 传导节奏明确: {action}")
            if pressure_targets:
                opportunities.append(f"✅ 风格切换观察: {pressure_targets[0]}")
        elif self.role == AgentRole.POLICY_SENSITIVE:
            opportunities.extend(
                self._context_points_for_role(
                    market_context_lines,
                    category="opportunities",
                )
            )
        elif self.role == AgentRole.MARGIN_TRADING:
            opportunities.extend(
                self._context_points_for_role(
                    market_context_lines,
                    category="opportunities",
                )
            )
        elif self.role == AgentRole.NORTHBOUND:
            opportunities.extend(
                self._context_points_for_role(
                    market_context_lines,
                    category="opportunities",
                )
            )
        elif self.role == AgentRole.RETAIL_MOOD:
            opportunities.extend(
                self._context_points_for_role(
                    market_context_lines,
                    category="opportunities",
                )
            )

        opportunities.extend(
            self._context_points_for_role(
                market_context_lines,
                category="opportunities",
            )
        )
        opportunities.extend(generic_opportunities)
        return opportunities[:3]  # 最多返回3个机会因素

    def _context_points_for_role(
        self,
        market_context_lines: tuple[str, ...],
        *,
        category: Literal["arguments", "risks", "opportunities"],
    ) -> list[str]:
        if not market_context_lines:
            return []
        matched: list[str] = []
        for raw in market_context_lines:
            line = str(raw).strip()
            if not line:
                continue
            if self.role == AgentRole.NORTHBOUND and "北向资金" in line:
                matched.append(f"上下文: {line}")
            elif self.role == AgentRole.MARGIN_TRADING and "融资情绪" in line:
                matched.append(f"上下文: {line}")
            elif self.role == AgentRole.POLICY_SENSITIVE and any(
                marker in line for marker in ("政策", "监管", "发改委", "工信部")
            ):
                matched.append(f"上下文: {line}")
            elif self.role == AgentRole.CROSS_MARKET and (
                "传导推演[" in line
                or "传导链:" in line
                or "确认信号:" in line
                or "失效条件:" in line
                or "证据堆栈:" in line
                or "来源质量:" in line
                or "海外风险:" in line
                or "情报时效:" in line
                or "综合风向:" in line
            ):
                matched.append(f"上下文: {line}")
            elif self.role == AgentRole.SECTOR_LEADER and "传导推演[" in line:
                matched.append(f"上下文: {line}")
            elif (
                self.role == AgentRole.BULL
                and category == "opportunities"
                and ("偏多" in line or "偏强" in line or "动作 优先复核" in line)
            ):
                matched.append(f"上下文: {line}")
            elif (
                self.role == AgentRole.BEAR
                and category == "risks"
                and (
                    "偏空" in line
                    or "偏弱" in line
                    or "抓取失败" in line
                    or "动作 观察为主" in line
                )
            ):
                matched.append(f"上下文: {line}")
            elif (
                self.role == AgentRole.RISK_CONTROL
                and category == "risks"
                and (
                    "偏弱" in line
                    or "抓取失败" in line
                    or "部分可用" in line
                    or "动作 观察为主" in line
                )
            ):
                matched.append(f"上下文: {line}")
            elif self.role == AgentRole.RETAIL_MOOD and (
                "全局雷达" in line or "传导推演[" in line
            ):
                matched.append(f"上下文: {line}")
            if matched:
                break
        return matched

    @staticmethod
    def _cross_market_support_event_count(pick: PickResult) -> int:
        return _nonnegative_int(
            (pick.metrics or {}).get("cross_market_support_event_count", 0)
        )

    @staticmethod
    def _cross_market_conflict_event_count(pick: PickResult) -> int:
        return _nonnegative_int(
            (pick.metrics or {}).get("cross_market_conflict_event_count", 0)
        )

    @classmethod
    def _cross_market_evidence_counts(
        cls,
        pick: PickResult,
        *,
        market_context_lines: tuple[str, ...] = (),
    ) -> tuple[int, int]:
        support_count = cls._cross_market_support_event_count(pick)
        conflict_count = cls._cross_market_conflict_event_count(pick)
        if support_count > 0 or conflict_count > 0:
            return support_count, conflict_count
        summary = cls._cross_market_evidence_stack_summary(
            pick,
            market_context_lines=market_context_lines,
        )
        return cls._parse_cross_market_evidence_stack_summary(summary)

    @classmethod
    def _cross_market_evidence_stack_summary(
        cls,
        pick: PickResult,
        *,
        market_context_lines: tuple[str, ...] = (),
    ) -> str:
        summary = str(
            pick.metrics.get("cross_market_evidence_stack_summary", "") or ""
        ).strip()
        if summary:
            return summary
        prefix = "证据堆栈:"
        for raw in market_context_lines:
            text = str(raw).strip()
            if text.startswith(prefix):
                return text[len(prefix) :].strip()
        return ""

    @classmethod
    def _cross_market_source_quality(
        cls,
        pick: PickResult,
        *,
        market_context_lines: tuple[str, ...] = (),
    ) -> tuple[str, int]:
        label = str(
            pick.metrics.get("cross_market_source_quality_label", "") or ""
        ).strip()
        score = int(pick.metrics.get("cross_market_source_quality_score", 0) or 0)
        if label:
            return label, score
        summary = ""
        for raw in market_context_lines:
            text = str(raw).strip()
            if text.startswith("来源质量:"):
                summary = text[len("来源质量:") :].strip()
                break
        if not summary:
            return "", 0
        if "高价值" in summary:
            return summary, 4
        if "多源/权威" in summary:
            return summary, 3
        if "主流媒体" in summary:
            return summary, 2
        return summary, 1

    @classmethod
    def _has_cross_market_evidence(
        cls,
        pick: PickResult,
        *,
        market_context_lines: tuple[str, ...] = (),
    ) -> bool:
        if any(
            marker in str(line).strip()
            for line in market_context_lines
            for marker in ("消息结果: 无可用新闻记录", "无可用新闻记录")
        ):
            return False
        metrics = pick.metrics or {}
        evidence_fields = (
            "cross_market_supporting_evidence",
            "cross_market_contradicting_evidence",
            "cross_market_evidence_points",
        )
        if any(
            any(
                not _is_non_evidence_text(item)
                for item in _text_items(metrics.get(field))
            )
            for field in evidence_fields
        ):
            return True
        support_count, conflict_count = cls._cross_market_evidence_counts(
            pick,
            market_context_lines=market_context_lines,
        )
        if support_count > 0 or conflict_count > 0:
            return True
        return any(
            str(line).strip().startswith("跨市证据:")
            and not _is_non_evidence_text(str(line).strip()[5:])
            for line in market_context_lines
        )

    @staticmethod
    def _cross_market_metric_texts(
        pick: PickResult,
        field: str,
    ) -> tuple[str, ...]:
        return _text_items((pick.metrics or {}).get(field))

    @staticmethod
    def _parse_cross_market_evidence_stack_summary(summary: str) -> tuple[int, int]:
        text = str(summary or "").strip()
        if not text:
            return (0, 0)
        support_match = re.search(r"同向\s*(\d+)\s*条", text)
        conflict_match = re.search(r"反向\s*(\d+)\s*条", text)
        return (
            int(support_match.group(1)) if support_match else 0,
            int(conflict_match.group(1)) if conflict_match else 0,
        )

    def respond_to_counterarguments(
        self,
        my_opinion: AgentOpinion,
        others_opinions: list[AgentOpinion],
    ) -> AgentOpinion:
        """回应对手的质疑"""
        updated_opinion = AgentOpinion(
            agent_id=my_opinion.agent_id,
            role=my_opinion.role,
            stance=my_opinion.stance,
            confidence=my_opinion.confidence,
            arguments=my_opinion.arguments.copy(),
            risk_factors=my_opinion.risk_factors.copy(),
            opportunity_factors=my_opinion.opportunity_factors.copy(),
            counterarguments=[],
            counterargument_roles=[],
            peer_reviewed_roles=[],
            rebuttal_records=[],
            llm_advisory_points=my_opinion.llm_advisory_points,
            final_position=my_opinion.final_position,
        )

        counterargs: list[str] = []
        rebuttal_records: list[RebuttalRecord] = []
        for other in others_opinions:
            if other.role == self.role:
                continue

            peer_point = self._first_meaningful_point(
                other.arguments or other.risk_factors or other.opportunity_factors
            )
            if not peer_point:
                continue
            updated_opinion.peer_reviewed_roles.append(other.role.value)

            # 只有多空方向相反才构成反驳；中性意见只能作为待确认事项。
            if self._should_counter(other, updated_opinion):
                record = self._generate_counterargument(other, updated_opinion)
                rebuttal_records.append(record)
                counterargs.append(self._format_rebuttal(record))
                updated_opinion.counterargument_roles.append(other.role.value)

        updated_opinion.counterarguments.extend(counterargs)

        updated_opinion.rebuttal_records.extend(rebuttal_records)

        # 如果反对意见太多，降低信心
        if len(counterargs) > 2:
            updated_opinion.confidence *= 0.85

        return updated_opinion

    @staticmethod
    def _format_rebuttal(record: RebuttalRecord) -> str:
        return (
            f"复核对象={record.challenged_role}; "
            f"被挑战主张={record.challenged_claim}; "
            f"反驳理由={record.rebuttal_reason}"
        )

    @staticmethod
    def _first_meaningful_point(points: list[str]) -> str:
        """Return one concrete peer point, or an empty string if none exists."""
        return next(
            (
                str(point).strip()
                for point in points
                if str(point).strip() and not _is_non_evidence_text(point)
            ),
            "",
        )

    def _should_counter(
        self,
        other: AgentOpinion,
        my_opinion: AgentOpinion,
    ) -> bool:
        """判断是否应该反驳"""
        if {
            other.stance,
            my_opinion.stance,
        } == {"bullish", "bearish"}:
            return True

        # 高分候选上的反方角色通常保持 neutral，而不是硬造 bearish
        # 投票。只要它给出了可核验的风险/失效条件，也必须形成真实质询，
        # 否则第二轮只是“读过观点”而不是讨论。
        if my_opinion.stance == "neutral" and other.stance in {
            "bullish",
            "bearish",
        }:
            return bool(self._first_meaningful_point(my_opinion.risk_factors))
        return False

    def _generate_counterargument(
        self,
        other: AgentOpinion,
        my_opinion: AgentOpinion,
    ) -> RebuttalRecord:
        peer_point = self._first_meaningful_point(
            other.arguments or other.risk_factors or other.opportunity_factors
        )
        if not peer_point:
            raise ValueError(
                "cannot generate rebuttal without a substantive peer claim"
            )
        if my_opinion.stance == "neutral":
            reason = (
                "当前维持中性；该主张仍缺少风险条件的确认，"
                "若失效条件出现则不支持继续提高优先级"
            )
        else:
            reason = (
                f"当前{my_opinion.stance}立场与该主张方向相反；"
                "若该主张成立，当前方向假设将失效"
            )
        return RebuttalRecord(
            challenged_role=other.role.value,
            challenged_claim=peer_point,
            rebuttal_reason=reason,
            challenged_stance=other.stance,
            opposing_stance=my_opinion.stance,
        )


class AShareDebateCoordinator:
    """A股市场委员会协调器 - 管理多 Agent 讨论流程"""

    def __init__(
        self,
        enable_llm: bool = False,
        max_rounds: int = 2,
        thresholds_version: str = "",
        regime: str = "",
        data_source: str = "",
        language: str = "zh-CN",
        roles: tuple[AgentRole, ...] | None = None,
        role_runtime: tuple[DebateRoleRuntime, ...] | None = None,
        task_id: str | None = None,
        debate_deadline_seconds: float = 30.0,
        heartbeat_callback: Callable[[], None] | None = None,
        heartbeat_interval_seconds: float = 1.0,
    ):
        self.enable_llm = enable_llm
        self.thresholds_version = thresholds_version
        self.regime = regime
        self.data_source = data_source
        self.language = language
        self.task_id = str(task_id or os.getenv("AQSP_RUN_TASK_ID", "")).strip()
        self.debate_deadline_seconds = max(0.0, float(debate_deadline_seconds))
        self.heartbeat_callback = heartbeat_callback
        self.heartbeat_interval_seconds = max(0.05, float(heartbeat_interval_seconds))
        self._deadline_monotonic: float | None = None
        self._result: DebateResult | None = None
        self.roles = DEFAULT_AGENT_ROLE_ORDER if roles is None else tuple(roles)
        self.role_runtime = {item.role: item for item in (role_runtime or ())}
        # One round cannot contain a peer response, so it is not a debate.
        self.max_rounds = max(2, int(max_rounds))
        self.agents = self._create_agents()

        from aqsp.briefing.debate_tracker import DebatePerformanceTracker

        self.tracker = DebatePerformanceTracker(task_id=self.task_id or None)

    def _create_agents(self) -> list[AShareDebateAgent]:
        """创建所有辩论 Agent"""
        agents: list[AShareDebateAgent] = []
        for role in self.roles:
            runtime = self.role_runtime.get(role.value)
            agents.append(
                AShareDebateAgent(
                    role,
                    enable_llm=(
                        self.enable_llm if runtime is None else runtime.enable_llm
                    ),
                    language=self.language,
                    llm_provider="" if runtime is None else runtime.provider,
                    llm_model="" if runtime is None else runtime.model,
                )
            )
        return agents

    def run_debate(
        self,
        pick: PickResult,
        df: pd.DataFrame,
        signal_date: str = "",
        *,
        market_context_lines: tuple[str, ...] = (),
        runtime_blocked: bool = False,
        runtime_blocker: str = "",
        task_id: str | None = None,
    ) -> DebateResult:
        """运行完整辩论流程"""
        from aqsp.core.time import now_shanghai

        if task_id is not None:
            scoped_task_id = str(task_id or "").strip()
            if scoped_task_id != self.task_id:
                self.task_id = scoped_task_id
                self.tracker.set_task_scope(scoped_task_id or None)

        result = DebateResult(
            debate_id=uuid4().hex,
            symbol=pick.symbol,
            name=pick.name,
            original_score=pick.score,
            rating=pick.rating,
            debate_rounds_requested=self.max_rounds,
            expected_roles=tuple(agent.role.value for agent in self.agents),
            data_status="empty" if df.empty else "available",
            data_note=(
                "行情数据为空，讨论只保留阻塞记录，不形成证据结论。" if df.empty else ""
            ),
            thresholds_version=self.thresholds_version,
            regime=self.regime,
            data_source=self.data_source,
            related_signal_date=(
                signal_date
                or str(pick.date or "").strip()
                or now_shanghai().date().isoformat()
            ),
            candidate_fingerprint=str(
                (pick.metrics or {}).get("candidate_fingerprint")
                or (pick.metrics or {}).get("debate_candidate_fingerprint")
                or ""
            ).strip(),
            market_context_lines=tuple(
                str(line).strip() for line in market_context_lines if str(line).strip()
            ),
            runtime_status="blocked" if runtime_blocked else "paper_review",
            realtime_blocked=runtime_blocked,
            runtime_blocker=str(runtime_blocker or "").strip(),
            task_id=self.task_id,
            deadline_seconds=self.debate_deadline_seconds,
            deterministic_score=float(pick.score),
            advisory_only=True,
        )
        (
            result.real_message_evidence,
            result.rule_transmission_evidence,
            result.pending_confirmations,
        ) = self._extract_evidence_provenance(pick, result.market_context_lines)
        result.cross_market_evidence = self._extract_cross_market_evidence(
            pick,
            result.market_context_lines,
        )
        result.role_selection_summary = summarize_context_agent_roles(
            pick,
            selected_roles=tuple(agent.role for agent in self.agents),
            market_context_lines=result.market_context_lines,
            language=self.language,
        )
        result.role_selection_plan = summarize_context_role_plan(
            selected_roles=tuple(agent.role for agent in self.agents),
            pick=pick,
            market_context_lines=result.market_context_lines,
            language=self.language,
        )
        (
            result.cross_market_support_event_count,
            result.cross_market_conflict_event_count,
            result.cross_market_evidence_stack_summary,
        ) = self._extract_cross_market_evidence_context(result.market_context_lines)
        if not result.cross_market_evidence_stack_summary:
            metrics = pick.metrics or {}
            result.cross_market_support_event_count = _nonnegative_int(
                metrics.get("cross_market_support_event_count", 0)
            )
            result.cross_market_conflict_event_count = _nonnegative_int(
                metrics.get("cross_market_conflict_event_count", 0)
            )
            result.cross_market_evidence_stack_summary = str(
                metrics.get("cross_market_evidence_stack_summary", "") or ""
            ).strip()

        if df.empty:
            result.runtime_status = "blocked"
            result.realtime_blocked = True
            result.runtime_blocker = "行情数据为空"
            result.recommended_adjustment = "keep"
            result.adjustment_weight = 0.0
            result.adjusted_score = result.original_score
            result.deterministic_score_unchanged = True
            result.primary_risk_gate = "行情数据为空"
            result.research_verdict = "结论阻断：行情数据为空，仅记录待补行情。"
            result.next_trigger = (
                "行情数据恢复并通过 freshness 校验后，重新运行多 Agent 讨论。"
            )
            quality = _audit_result_quality(result)
            result.failure = "讨论链路未通过审计: " + "、".join(quality.issues)
            return result

        if not self.agents:
            result.failure = "未配置可用讨论角色"
            result.runtime_status = "blocked"
            result.runtime_blocker = result.failure
            result.adjusted_score = result.original_score
            return result

        self._result = result
        self._deadline_monotonic = (
            time.monotonic() + self.debate_deadline_seconds
            if self.debate_deadline_seconds > 0
            else None
        )
        try:
            self._heartbeat()
            final_opinions = self._run_debate_rounds(
                result,
                pick,
                df,
                market_context_lines=result.market_context_lines,
            )
        except DebateDeadlineExceeded as exc:
            result.deadline_exceeded = True
            result.failure = f"讨论执行超时: {exc}"
            result.runtime_status = "blocked"
            result.realtime_blocked = True
            result.runtime_blocker = result.failure
            result.adjusted_score = result.original_score
            result.deterministic_score_unchanged = True
            return result
        except Exception as exc:
            logger.exception("多 Agent 讨论失败: %s", exc)
            result.failure = f"讨论执行失败: {type(exc).__name__}: {exc}"
            result.runtime_status = "blocked"
            result.realtime_blocked = True
            result.runtime_blocker = result.failure
            result.adjusted_score = result.original_score
            result.deterministic_score_unchanged = True
            return result
        finally:
            self._deadline_monotonic = None
            self._result = None

        self._synthesize_result(result, final_opinions)
        if not result.pending_confirmations:
            result.pending_confirmations = tuple(result.watch_items[:3])

        agent_ids = {agent.role: agent.agent_id for agent in self.agents}
        self._calculate_adjustment(result, agent_ids)

        if runtime_blocked:
            self._apply_runtime_block(result, runtime_blocker)

        for opinion in final_opinions:
            result.agent_performance_snapshot[opinion.role.value] = (
                self.tracker.get_agent_metrics(opinion.role, opinion.agent_id)
            )

        if result.deterministic_score != result.original_score:
            raise RuntimeError(
                "debate advisory layer attempted to change deterministic score"
            )
        result.deterministic_score_unchanged = True
        result.advisory_only = True

        quality = _audit_result_quality(result)
        if not quality.passed:
            result.failure = "讨论链路未通过审计: " + "、".join(quality.issues)

        return result

    @staticmethod
    def _extract_evidence_provenance(
        pick: PickResult,
        market_context_lines: tuple[str, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """Separate sourced messages, deterministic rules, and checks still pending."""
        metrics = pick.metrics or {}
        messages: list[str] = []
        lead = str(metrics.get("news_catalyst_lead", "") or "").strip()
        title = str(metrics.get("news_catalyst_title", "") or "").strip()
        source = str(metrics.get("news_catalyst_source", "") or "").strip()
        published_at = str(metrics.get("news_catalyst_published_at", "") or "").strip()
        if lead or title:
            message = lead or title
            if not _is_non_evidence_text(message):
                metadata = " / ".join(
                    item
                    for item in (
                        f"来源 {source}" if source else "",
                        f"发布时间 {published_at}" if published_at else "",
                    )
                    if item
                )
                messages.append(f"{message}{f' ({metadata})' if metadata else ''}")
        for field_name in (
            "news_catalyst_supporting_evidence",
            "news_catalyst_contradicting_evidence",
        ):
            for item in _text_items(metrics.get(field_name)):
                if not _is_non_evidence_text(item):
                    text = item
                    messages.append(text)
        for raw in market_context_lines:
            message = _message_evidence_from_context_line(raw)
            if message:
                messages.append(message)

        rules: list[str] = []
        hypothesis = str(
            metrics.get("cross_market_transmission_hypothesis", "")
            or metrics.get("news_catalyst_transmission_hypothesis", "")
            or ""
        ).strip()
        if hypothesis and not _is_non_evidence_text(hypothesis):
            rules.append(f"传导假设: {hypothesis}")
        for field_name, label in (
            ("cross_market_transmission_path", "传导路径"),
            ("cross_market_chain_summary", "传导链"),
        ):
            for item in _text_items(metrics.get(field_name)):
                if not _is_non_evidence_text(item):
                    rules.append(f"{label}: {item}")
        for raw in market_context_lines:
            text = str(raw).strip()
            if text.startswith(
                ("传导推演[", "候选传导:", "传导链:")
            ) and not _is_non_evidence_text(text):
                rules.append(text)

        pending: list[str] = []
        for field_name, label in (
            ("cross_market_validation_signals", "确认"),
            ("cross_market_invalidation_signals", "失效"),
            ("cross_market_execution_watchpoints", "盘中观察"),
        ):
            for item in _text_items(metrics.get(field_name)):
                if not _is_non_evidence_text(item):
                    text = item
                    pending.append(f"{label}: {text}")
        for raw in market_context_lines:
            text = str(raw).strip()
            if text.startswith(("确认信号:", "失效条件:")):
                pending.append(text)

        def dedupe(values: list[str]) -> tuple[str, ...]:
            return tuple(dict.fromkeys(item for item in values if item))

        return dedupe(messages), dedupe(rules), dedupe(pending)

    @staticmethod
    def _extract_cross_market_evidence(
        pick: PickResult,
        market_context_lines: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Keep sourced cross-market evidence separate from its transmission rule."""
        metrics = pick.metrics or {}
        values: list[str] = []
        for field_name in (
            "cross_market_supporting_evidence",
            "cross_market_contradicting_evidence",
            "cross_market_evidence_points",
        ):
            values.extend(
                item
                for item in _text_items(metrics.get(field_name))
                if not _is_non_evidence_text(item)
            )
        for raw in market_context_lines:
            text = str(raw).strip()
            if text.startswith("跨市证据:"):
                value = text[len("跨市证据:") :].strip()
                if value and not _is_non_evidence_text(value):
                    values.append(value)
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _apply_runtime_block(result: DebateResult, blocker: str) -> None:
        """Keep the discussion, but make a stale/failed live run non-actionable."""
        reason = str(blocker or "实时数据未通过时效或来源校验").strip()
        result.runtime_status = "blocked"
        result.realtime_blocked = True
        result.runtime_blocker = reason
        result.recommended_adjustment = "keep"
        result.adjustment_weight = 0.0
        result.adjusted_score = result.original_score
        result.deterministic_score_unchanged = True
        result.adjustment_reason = (
            f"实时数据阻塞：{reason}；当前仅观察，不形成推荐；"
            "deterministic score 保持不变"
        )
        result.primary_risk_gate = f"实时数据阻塞: {reason}"
        result.research_verdict = f"实时数据阻塞，当前仅观察/阻塞：{reason}"
        result.next_trigger = (
            "实时数据恢复并通过 freshness 校验后，重新运行多 Agent 讨论。"
        )

    def _run_debate_rounds(
        self,
        result: DebateResult,
        pick: PickResult,
        df: pd.DataFrame,
        *,
        market_context_lines: tuple[str, ...] = (),
    ) -> list[AgentOpinion]:
        """运行辩论轮次"""
        round1_opinions = []
        for agent in self.agents:
            self._heartbeat()
            opinion = self._call_agent_with_deadline(
                lambda: agent.generate_initial_opinion(
                    pick,
                    df,
                    market_context_lines=market_context_lines,
                )
            )
            round1_opinions.append(opinion)
            self._heartbeat()

        result.rounds.append(
            DebateRound(
                round_num=1,
                opinions=round1_opinions,
                summary=self._summarize_round(round1_opinions),
                cross_opinions=self._collect_cross_opinions(round1_opinions),
                interaction_pairs=(),
            )
        )

        for round_num in range(2, self.max_rounds + 1):
            prev_round = result.rounds[-1]
            current_opinions = []

            for agent in self.agents:
                self._heartbeat()
                my_prev = next(
                    (op for op in prev_round.opinions if op.agent_id == agent.agent_id),
                    None,
                )
                if my_prev is None:
                    logger.error(
                        f"辩论链路断裂: Agent {agent.agent_id} 缺失第 {round_num - 1} 轮观点，无法继续辩论"
                    )
                    raise ValueError(f"Agent {agent.agent_id} 的观点缺失，辩论中止")
                updated = self._call_agent_with_deadline(
                    lambda: agent.respond_to_counterarguments(
                        my_prev,
                        prev_round.opinions,
                    )
                )
                current_opinions.append(updated)
                self._heartbeat()

            result.rounds.append(
                DebateRound(
                    round_num=round_num,
                    opinions=current_opinions,
                    summary=self._summarize_round(current_opinions),
                    cross_opinions=self._collect_cross_opinions(current_opinions),
                    interaction_pairs=self._collect_interaction_pairs(current_opinions),
                )
            )

        return result.rounds[-1].opinions

    def _call_agent_with_deadline(
        self,
        operation: Callable[[], AgentOpinion],
    ) -> AgentOpinion:
        """在模型调用阻塞时继续 heartbeat，超时不等待后台线程收尾。"""
        if self._deadline_monotonic is None:
            return operation()

        outcome: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                outcome.put((True, operation()))
            except BaseException as exc:  # 在线程边界重新抛回主流程
                outcome.put((False, exc))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while True:
            self._heartbeat()
            remaining = self._deadline_monotonic - time.monotonic()
            if remaining <= 0:
                raise DebateDeadlineExceeded(
                    f"超过 {self.debate_deadline_seconds:.1f}s deadline"
                )
            try:
                succeeded, value = outcome.get(
                    timeout=min(self.heartbeat_interval_seconds, remaining)
                )
            except queue.Empty:
                continue
            if succeeded:
                return value  # type: ignore[return-value]
            raise value  # type: ignore[misc]

    def _heartbeat(self) -> None:
        """先刷新存活信号，再检查 deadline，避免长调用被误判为 stale。"""
        if self._result is not None:
            self._result.heartbeat_count += 1
            from aqsp.core.time import now_shanghai

            self._result.last_heartbeat_at = now_shanghai().isoformat(
                timespec="seconds"
            )
        if self.heartbeat_callback is not None:
            try:
                self.heartbeat_callback()
            except Exception:  # 心跳是观测旁路，不能改变讨论结论
                logger.warning("多 Agent 讨论 heartbeat callback failed", exc_info=True)
        if (
            self._deadline_monotonic is not None
            and time.monotonic() > self._deadline_monotonic
        ):
            raise DebateDeadlineExceeded(
                f"超过 {self.debate_deadline_seconds:.1f}s deadline"
            )

    def _summarize_round(self, opinions: list[AgentOpinion]) -> str:
        vote_counts: dict[str, int] = {"bullish": 0, "bearish": 0, "neutral": 0}
        for opinion in opinions:
            vote_counts[opinion.stance] = vote_counts.get(opinion.stance, 0) + 1

        parts = [
            f"看多{vote_counts['bullish']} / 看空{vote_counts['bearish']} / 中性{vote_counts['neutral']}"
        ]
        bull_line = self._round_focus_line(
            opinions,
            preferred_role=AgentRole.BULL,
            fallback_stance="bullish",
            category="arguments",
        )
        if bull_line:
            parts.append(f"看多主张: {bull_line}")
        bear_line = self._round_focus_line(
            opinions,
            preferred_role=AgentRole.BEAR,
            fallback_stance="bearish",
            category="arguments",
        )
        if bear_line:
            parts.append(f"看空主张: {bear_line}")
        risk_line = self._round_focus_line(
            opinions,
            preferred_role=AgentRole.RISK_CONTROL,
            fallback_stance="bearish",
            category="risks",
        )
        if risk_line:
            parts.append(f"风控焦点: {risk_line}")
        cross_line = self._round_focus_line(
            opinions,
            preferred_role=AgentRole.CROSS_MARKET,
            fallback_stance="bullish",
            category="arguments",
        )
        if cross_line:
            parts.append(f"跨市焦点: {cross_line}")
        clash_line = self._counterargument_digest(opinions)
        if clash_line:
            parts.append(f"交锋焦点: {clash_line}")
        return "；".join(parts)

    @staticmethod
    def _collect_interaction_pairs(
        opinions: list[AgentOpinion],
    ) -> tuple[tuple[str, str], ...]:
        pairs: list[tuple[str, str]] = []
        for opinion in opinions:
            source = opinion.role.value
            for target in (
                *opinion.counterargument_roles,
                *opinion.peer_reviewed_roles,
            ):
                pair = (source, str(target).strip())
                if pair[1] and pair not in pairs:
                    pairs.append(pair)
        return tuple(pairs)

    def _collect_cross_opinions(
        self,
        opinions: list[AgentOpinion],
    ) -> dict[str, list[str]]:
        cross: dict[str, list[str]] = {}
        for opinion in opinions:
            if opinion.counterarguments:
                cross[opinion.role.value] = opinion.counterarguments[:2]
        return cross

    def _round_focus_line(
        self,
        opinions: list[AgentOpinion],
        *,
        preferred_role: AgentRole,
        fallback_stance: Literal["bullish", "bearish", "neutral"],
        category: Literal["arguments", "risks"],
    ) -> str:
        selected = next(
            (item for item in opinions if item.role == preferred_role), None
        )
        if selected is None:
            candidates = [item for item in opinions if item.stance == fallback_stance]
            if not candidates:
                return ""
            selected = max(candidates, key=lambda item: item.confidence)
        if category == "arguments":
            points = selected.arguments or selected.opportunity_factors
        else:
            points = selected.risk_factors or selected.arguments
        point = self._first_meaningful_point(points)
        if not point:
            return ""
        return f"{agent_role_label(selected.role, self.language)}: {point}"

    def _counterargument_digest(self, opinions: list[AgentOpinion]) -> str:
        items: list[str] = []
        for opinion in sorted(opinions, key=lambda item: item.confidence, reverse=True):
            counter = self._first_meaningful_point(opinion.counterarguments)
            if not counter:
                continue
            targets = tuple(
                dict.fromkeys(
                    (*opinion.counterargument_roles, *opinion.peer_reviewed_roles)
                )
            )
            if targets:
                items.append(
                    f"{agent_role_label(opinion.role, self.language)}质询"
                    f"{'、'.join(targets[:3])}"
                )
            if len(items) >= 2:
                break
        return "；".join(items)

    @staticmethod
    def _first_meaningful_point(points: list[str]) -> str:
        for raw in points:
            text = str(raw).strip()
            if text and not _is_non_evidence_text(text):
                return text
        return ""

    @staticmethod
    def _first_falsifiable_point(points: list[str]) -> str:
        for raw in points:
            text = str(raw).strip()
            if (
                text
                and not _is_non_evidence_text(text)
                and _contains_falsifiable_marker(text)
            ):
                return text
        return ""

    def _synthesize_result(
        self,
        result: DebateResult,
        final_opinions: list[AgentOpinion],
    ) -> None:
        """汇总辩论结果"""
        vote_counts: dict[str, int] = {"bullish": 0, "bearish": 0, "neutral": 0}

        for opinion in final_opinions:
            result.final_vote[opinion.role] = opinion.stance
            vote_counts[opinion.stance] += 1
            result.risk_warnings.extend(opinion.risk_factors)
            result.opportunity_highlights.extend(opinion.opportunity_factors)

        result.risk_warnings = list(dict.fromkeys(result.risk_warnings))[:5]
        result.opportunity_highlights = list(
            dict.fromkeys(result.opportunity_highlights)
        )[:5]

        max_vote = max(vote_counts.values())
        # A tie must remain a disagreement. Dict insertion order must not
        # turn a bull/bear tie into a bullish conclusion.
        consensus_positions = [
            pos for pos, count in vote_counts.items() if count == max_vote and count > 0
        ]
        result.final_consensus = (
            consensus_positions[0] if len(consensus_positions) == 1 else "split"
        )

        result.adjustment_reason = (
            f"多头{vote_counts['bullish']}票 vs 空头{vote_counts['bearish']}票"
        )
        result.support_points = self._build_support_points(final_opinions, result)
        result.opposition_points = self._build_opposition_points(final_opinions, result)
        result.watch_items = self._build_watch_items(final_opinions, result)
        if not result.support_points:
            result.support_points = ("尚未形成明确支持证据，先保持观察。",)
        if not result.opposition_points:
            result.opposition_points = ("尚未形成明确反对证据，仍需风险复核。",)
        if not result.watch_items:
            result.watch_items = ("等待实时量价、消息与跨市场映射确认。",)
        result.falsifiable_conditions = self._build_falsifiable_conditions(
            final_opinions, result
        )
        if not result.falsifiable_conditions and _has_supporting_result_evidence(
            result
        ):
            result.falsifiable_conditions = (
                "失效条件: 支持证据未在盘中量价承接或板块扩散中得到确认。",
            )
        result.primary_risk_gate = self._build_primary_risk_gate(result)
        result.next_trigger = self._build_next_trigger(result)
        result.research_verdict = self._build_research_verdict(result)

    def _build_support_points(
        self,
        final_opinions: list[AgentOpinion],
        result: DebateResult,
    ) -> tuple[str, ...]:
        cross_market_points: list[str] = []
        other_points: list[str] = []
        confirmation_signal = self._context_line_value(
            result.market_context_lines,
            prefix="确认信号:",
        )
        for opinion in final_opinions:
            if opinion.stance != "bullish":
                continue
            if opinion.role == AgentRole.CROSS_MARKET:
                points = self._preferred_cross_market_points(
                    opinion.opportunity_factors,
                    preferred_markers=(
                        "来源质量较高",
                        "同向证据",
                        "跨市传导匹配",
                        "传导节奏明确",
                    ),
                    fallback=opinion.arguments,
                    limit=2,
                )
                for point in points:
                    cross_market_points.append(
                        f"{agent_role_label(opinion.role, self.language)}: {point}"
                    )
                continue
            point = self._first_meaningful_point(
                opinion.opportunity_factors or opinion.arguments
            )
            if point:
                other_points.append(
                    f"{agent_role_label(opinion.role, self.language)}: {point}"
                )
        if confirmation_signal:
            cross_market_points.append(f"跨市传导: 验证重点 {confirmation_signal}")
        points = cross_market_points + other_points
        if not points:
            for point in result.opportunity_highlights[:2]:
                text = str(point).strip()
                if text:
                    points.append(text)
        return self._dedupe_points(points, limit=3)

    def _build_opposition_points(
        self,
        final_opinions: list[AgentOpinion],
        result: DebateResult,
    ) -> tuple[str, ...]:
        points: list[str] = []
        invalidation_signal = self._context_line_value(
            result.market_context_lines,
            prefix="失效条件:",
        )
        has_cross_market_evidence = bool(
            result.cross_market_support_event_count
            or result.cross_market_conflict_event_count
            or result.cross_market_evidence_stack_summary.strip()
        ) and not any(
            marker in line
            for line in result.market_context_lines
            for marker in ("消息结果: 无可用新闻记录", "无可用新闻记录")
        )
        if invalidation_signal:
            points.append(f"跨市传导: 失效条件 {invalidation_signal}")
        ordered_opinions = sorted(
            final_opinions,
            key=lambda opinion: {
                AgentRole.CROSS_MARKET: 0,
                AgentRole.RISK_CONTROL: 1,
                AgentRole.BEAR: 2,
            }.get(opinion.role, 3),
        )
        for opinion in ordered_opinions:
            has_conditional_challenge = any(
                "反方可证伪主张" in str(item) for item in opinion.risk_factors
            )
            if (
                opinion.role == AgentRole.CROSS_MARKET
                and not has_cross_market_evidence
                and not has_conditional_challenge
            ):
                continue
            if opinion.stance == "bearish" or opinion.role in {
                AgentRole.RISK_CONTROL,
                AgentRole.CROSS_MARKET,
            }:
                point = (
                    self._preferred_cross_market_point(
                        opinion.risk_factors,
                        preferred_markers=(
                            "反方可证伪主张",
                            "反向证据",
                            "失效条件",
                            "跨市传导证据偏弱",
                            "海外叙事未必立刻传到A股",
                        ),
                        fallback=opinion.arguments,
                    )
                    if opinion.role == AgentRole.CROSS_MARKET
                    else (
                        self._first_falsifiable_point(opinion.risk_factors)
                        or self._first_meaningful_point(
                            opinion.risk_factors or opinion.arguments
                        )
                    )
                )
                if point:
                    points.append(
                        f"{agent_role_label(opinion.role, self.language)}: {point}"
                    )
            elif opinion.rebuttal_records:
                point = self._first_meaningful_point(opinion.counterarguments)
                if point:
                    points.append(
                        f"{agent_role_label(opinion.role, self.language)}反方质询: {point}"
                    )
        if not points:
            for point in result.risk_warnings[:2]:
                text = str(point).strip()
                if text:
                    points.append(text)
        return self._dedupe_points(points, limit=3)

    def _build_watch_items(
        self,
        final_opinions: list[AgentOpinion],
        result: DebateResult,
    ) -> tuple[str, ...]:
        points: list[str] = []
        confirmation_signal = self._context_line_value(
            result.market_context_lines,
            prefix="确认信号:",
        )
        invalidation_signal = self._context_line_value(
            result.market_context_lines,
            prefix="失效条件:",
        )
        if confirmation_signal:
            points.append(f"先确认 {confirmation_signal}。")
        if invalidation_signal:
            points.append(f"若出现 {invalidation_signal}，则按跨市逻辑失效处理。")
        evidence_stack = self._context_line_value(
            result.market_context_lines,
            prefix="证据堆栈:",
        )
        if evidence_stack:
            if "反向" in evidence_stack:
                points.append("跨市证据已出现反向分歧，先核对A股扩散是否还能持续。")
            elif "同向" in evidence_stack:
                points.append("跨市证据连续强化，优先核对A股映射是否同步放量。")
        if result.disagreement_score >= 0.5:
            points.append("分歧较大，优先核对开盘承接与量价延续。")
        if any("北向资金" in line for line in result.market_context_lines):
            points.append("观察北向强弱是否在次日延续，避免只是一日交易性流入。")
        if any("融资情绪" in line for line in result.market_context_lines):
            points.append("观察杠杆拥挤是否继续升温，防止高位踩踏。")
        has_cross_market_evidence = bool(
            result.cross_market_support_event_count
            or result.cross_market_conflict_event_count
            or result.cross_market_evidence_stack_summary.strip()
        ) and not any(
            marker in line
            for line in result.market_context_lines
            for marker in ("消息结果: 无可用新闻记录", "无可用新闻记录")
        )
        if has_cross_market_evidence and any(
            "海外风险:" in line for line in result.market_context_lines
        ):
            points.append("核对海外风险线索是否延续，避免隔夜外盘噪音误导。")
        if any("传导推演[" in line for line in result.market_context_lines):
            points.append("核对跨市场映射是否真的被A股主线接力，避免只停留在题材联想。")
        if any("动作 观察为主" in line for line in result.market_context_lines):
            points.append("跨市场线索证据偏弱，先按观察处理，不急于提升优先级。")
        if any("动作 优先复核" in line for line in result.market_context_lines):
            points.append("跨市场线索证据较强，可优先核对是否进入纸面复核名单。")
        cross_market = next(
            (item for item in final_opinions if item.role == AgentRole.CROSS_MARKET),
            None,
        )
        if cross_market is not None:
            if cross_market.stance == "bullish":
                points.append(
                    "跨市传导角色偏多，先核对板块共振和龙头承接是否同步出现。"
                )
            elif cross_market.stance == "bearish":
                points.append(
                    "跨市传导角色偏谨慎，先确认A股是否真的跟随，不要被海外叙事带偏。"
                )
        if any(
            "综合风向:" in line and "分化" in line
            for line in result.market_context_lines
        ):
            points.append("综合风向分化，优先等待开盘方向确认。")
        if any(
            "个股催化" in line or "全局雷达" in line
            for line in result.market_context_lines
        ):
            points.append("核对消息催化是否仍然有效，避免隔夜失真。")
        if any(
            "偏弱" in line or "抓取失败" in line or "部分可用" in line
            for line in result.market_context_lines
        ):
            points.append("外部上下文偏弱或不完整，降低对消息面的依赖。")
        risk_control = next(
            (item for item in final_opinions if item.role == AgentRole.RISK_CONTROL),
            None,
        )
        if risk_control is not None and risk_control.risk_factors:
            points.append("先确认流动性、止损位和可成交性，再决定是否进入纸面复核。")
        for opinion in final_opinions:
            counter = self._first_meaningful_point(opinion.counterarguments)
            if counter:
                targets = tuple(
                    dict.fromkeys(
                        (*opinion.counterargument_roles, *opinion.peer_reviewed_roles)
                    )
                )
                if targets:
                    points.append(
                        f"{agent_role_label(opinion.role, self.language)}已质询: "
                        f"{'、'.join(targets[:3])}"
                    )
        return self._dedupe_points(points, limit=3)

    def _build_primary_risk_gate(self, result: DebateResult) -> str:
        invalidation_signal = self._context_line_value(
            result.market_context_lines,
            prefix="失效条件:",
        )
        if invalidation_signal:
            return f"失效条件: {invalidation_signal}"
        for group in (result.opposition_points, tuple(result.risk_warnings)):
            point = self._first_meaningful_point(list(group))
            if point:
                return point
        return ""

    def _build_falsifiable_conditions(
        self,
        final_opinions: list[AgentOpinion],
        result: DebateResult,
    ) -> tuple[str, ...]:
        """Collect explicit checks that can invalidate the current thesis."""
        points: list[str] = []
        for raw in result.pending_confirmations:
            text = str(raw).strip()
            if text and any(
                marker in text for marker in ("失效", "确认", "若", "低于", "跌破")
            ):
                points.append(text)
        for raw in result.market_context_lines:
            text = str(raw).strip()
            if text.startswith(("失效条件:", "确认信号:")):
                points.append(text)
        for opinion in final_opinions:
            for raw in opinion.risk_factors + opinion.arguments:
                text = str(raw).strip()
                if text and any(
                    marker in text for marker in ("失效", "若", "跌破", "低于", "不达")
                ):
                    points.append(text)
        return self._dedupe_points(points, limit=4)

    def _build_next_trigger(self, result: DebateResult) -> str:
        confirmation_signal = self._context_line_value(
            result.market_context_lines,
            prefix="确认信号:",
        )
        if confirmation_signal:
            return f"先确认 {confirmation_signal}。"
        point = self._first_meaningful_point(list(result.watch_items))
        if point:
            return point
        if result.recommended_adjustment == "raise":
            return "若开盘承接与量价延续确认，再考虑转入纸面复核名单。"
        if result.recommended_adjustment == "lower":
            return "若阻塞条件缓解，再重新评估是否恢复观察顺位。"
        return "等待下一轮量价、流动性或主线共振确认。"

    def _build_research_verdict(self, result: DebateResult) -> str:
        support = self._preferred_research_support(result.support_points)
        risk_gate = str(result.primary_risk_gate or "").strip()
        quality = _audit_result_quality(result)
        # A named bear role is useful, but it is not the quality gate.  Risk
        # control, sector, or cross-market roles can all supply a bearish
        # thesis.  Without a real opposing claim and rebuttal, the committee
        # has not debated and must not emit a directional verdict.
        if not quality.real_opposition_recorded:
            return "结论阻断：未形成真实正反方交锋，仅记录观点和待确认条件。"
        if not quality.evidence_sufficient:
            return "结论阻断：缺少可核验证据，仅记录待补证据。"
        if not _has_substantive_debate_evidence(result):
            return "结论阻断：缺少可核验证据，仅记录待补证据。"
        near_review = any("纸面复核名单" in item for item in result.watch_items) or any(
            "动作 优先复核" in line or line.startswith("确认信号:")
            for line in result.market_context_lines
        )
        if result.recommended_adjustment == "raise":
            if result.disagreement_score >= 0.5 and risk_gate:
                return f"倾向优先纸面复核，但先卡住 {risk_gate}"
            if support:
                return f"倾向优先纸面复核，主因 {support}"
            return "倾向优先纸面复核。"
        if result.recommended_adjustment == "lower":
            if risk_gate:
                return f"倾向降级观察，当前先卡住 {risk_gate}"
            return "倾向降级观察，先不转入纸面复核。"
        if result.disagreement_score >= 0.5:
            if risk_gate:
                return f"倾向维持观察，分歧较大，先卡住 {risk_gate}"
            return "倾向维持观察，分歧较大。"
        if near_review and support and risk_gate:
            return f"倾向优先纸面复核，机会在 {support}，但卡点是 {risk_gate}"
        if near_review and support:
            return f"倾向优先纸面复核，主因 {support}"
        if support and risk_gate:
            return f"倾向继续观察，机会在 {support}，但卡点是 {risk_gate}"
        if support:
            return f"倾向继续观察，主因 {support}"
        if risk_gate:
            return f"倾向继续观察，先卡住 {risk_gate}"
        return "倾向维持观察，等待更多确认。"

    @staticmethod
    def _preferred_research_support(points: tuple[str, ...]) -> str:
        markers = (
            "来源质量较高",
            "同向证据",
            "验证重点",
            "跨市传导",
            "跨市传导匹配",
            "传导节奏明确",
        )
        for raw in points:
            text = str(raw).strip()
            if text and any(marker in text for marker in markers):
                return text
        return AShareDebateCoordinator._first_meaningful_point(list(points))

    @staticmethod
    def _dedupe_points(
        points: list[str],
        *,
        limit: int,
    ) -> tuple[str, ...]:
        cleaned: list[str] = []
        for raw in points:
            text = str(raw).strip()
            if not text or text in cleaned:
                continue
            cleaned.append(text)
            if len(cleaned) >= limit:
                break
        return tuple(cleaned)

    @staticmethod
    def _preferred_cross_market_point(
        points: list[str],
        *,
        preferred_markers: tuple[str, ...],
        fallback: list[str] | None = None,
    ) -> str:
        for marker in preferred_markers:
            for raw in points:
                text = str(raw).strip()
                if text and marker in text:
                    return text
            if fallback:
                for raw in fallback:
                    text = str(raw).strip()
                    if text and marker in text:
                        return text
        return AShareDebateCoordinator._first_meaningful_point(
            points or (fallback or [])
        )

    @staticmethod
    def _preferred_cross_market_points(
        points: list[str],
        *,
        preferred_markers: tuple[str, ...],
        fallback: list[str] | None = None,
        limit: int,
    ) -> list[str]:
        selected: list[str] = []
        pool = list(points) + list(fallback or [])
        for marker in preferred_markers:
            for raw in pool:
                text = str(raw).strip()
                if not text or marker not in text or text in selected:
                    continue
                selected.append(text)
                if len(selected) >= limit:
                    return selected
                break
        return selected

    @staticmethod
    def _context_line_value(
        lines: tuple[str, ...],
        *,
        prefix: str,
    ) -> str:
        for raw in lines:
            text = str(raw).strip()
            if text.startswith(prefix):
                return text[len(prefix) :].strip()
        return ""

    def _calculate_adjustment(
        self,
        result: DebateResult,
        agent_ids: dict[AgentRole, str],
    ) -> None:
        """计算评分调整"""
        debate_context = {
            "cross_market_support_event_count": result.cross_market_support_event_count,
            "cross_market_conflict_event_count": result.cross_market_conflict_event_count,
            "cross_market_evidence_stack_summary": result.cross_market_evidence_stack_summary,
        }
        history_summary = self.tracker.get_cross_market_context_history(
            debate_context=debate_context,
        )
        votes = {role: stance for role, stance in result.final_vote.items()}
        agent_weights = self.tracker.get_all_weights(
            agent_ids,
            regime=self.regime,
            debate_context=debate_context,
        )
        reliability_summaries = self.tracker.get_all_reliability_summaries(
            agent_ids,
            regime=self.regime,
            debate_context=debate_context,
        )
        if result.rounds:
            quality = _audit_result_quality(result)
            if (
                "missing_real_opposition" in quality.issues
                or "no_substantive_evidence" in quality.issues
            ):
                # A one-sided or evidence-free discussion cannot produce even
                # an advisory adjustment. Keep the deterministic score intact.
                result.disagreement_score = 1.0
                result.adjustment_weight = 0.0
                result.adjusted_score = result.original_score
                result.recommended_adjustment = "keep"
                result.adjustment_reason = (
                    "讨论质量门阻塞: "
                    + "、".join(quality.issues)
                    + "；不计算讨论层调整，确定性评分保持不变"
                )
                result.historical_context_note = history_summary.governance_note
                result.historical_context_bucket = history_summary.current_bucket
                result.historical_context_sample_count = (
                    history_summary.current_sample_count
                )
                result.historical_context_accuracy = history_summary.current_accuracy
                result.role_reliability_lines = tuple(
                    item.summary_line for item in reliability_summaries[:4]
                )
                return

        (
            adjustment_weight,
            disagreement_score,
            recommended_adjustment,
        ) = self.tracker.calculate_debate_adjustment(votes, agent_weights)

        risk_vote = result.final_vote.get(AgentRole.RISK_CONTROL)
        if risk_vote == "bearish" and adjustment_weight > 0:
            adjustment_weight = 0.0
            recommended_adjustment = "keep"
            result.risk_veto_applied = True
            result.risk_veto_reason = "风控角色明确看空，禁止讨论层上调候选评分"

        result.disagreement_score = disagreement_score
        result.adjustment_weight = adjustment_weight
        result.adjusted_score = result.original_score * (1 + adjustment_weight)
        result.recommended_adjustment = recommended_adjustment
        if result.risk_veto_applied:
            result.adjustment_reason += f"；{result.risk_veto_reason}"
        result.historical_context_note = history_summary.governance_note
        result.historical_context_bucket = history_summary.current_bucket
        result.historical_context_sample_count = history_summary.current_sample_count
        result.historical_context_accuracy = history_summary.current_accuracy
        result.role_reliability_lines = tuple(
            item.summary_line for item in reliability_summaries[:4]
        )

        if result.adjustment_weight > 0:
            result.adjustment_reason += f"，辩论倾向上调；附件参考分 {result.adjusted_score:.1f}，不改写系统评分"
        elif result.adjustment_weight < 0:
            result.adjustment_reason += f"，辩论倾向下调；附件参考分 {result.adjusted_score:.1f}，不改写系统评分"

    @staticmethod
    def _extract_cross_market_evidence_context(
        lines: tuple[str, ...],
    ) -> tuple[int, int, str]:
        summary = AShareDebateCoordinator._context_line_value(
            lines,
            prefix="证据堆栈:",
        )
        if not summary:
            summary = next(
                (
                    re.search(r"(同向\s*\d+\s*条｜反向\s*\d+\s*条)", line).group(1)
                    for line in lines
                    if re.search(r"(同向\s*\d+\s*条｜反向\s*\d+\s*条)", line)
                ),
                "",
            )
        if not summary:
            return (0, 0, "")
        support_match = re.search(r"同向\s*(\d+)\s*条", summary)
        conflict_match = re.search(r"反向\s*(\d+)\s*条", summary)
        return (
            int(support_match.group(1)) if support_match else 0,
            int(conflict_match.group(1)) if conflict_match else 0,
            summary,
        )


_NON_EVIDENCE_PHRASES = (
    "输入未提供",
    "当前输入不足",
    "无可用",
    "无可用新闻记录",
    "无新增",
    "尚未形成",
    "等待更多确认",
    "等待新证据",
    "未筛出",
    "抓取失败",
    "不能确认",
    "无法确认",
    "暂不确认",
    "不据此形成判断",
    "未提供额外风控证据",
    "保持风险复核",
    "未发现明显风险因素",
    "未出现需要反驳",
)


def _text_items(value: object) -> tuple[str, ...]:
    """Normalize scalar/list evidence fields without splitting strings into chars."""
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, (set, frozenset)):
        value = sorted(value, key=str)
    if isinstance(value, (list, tuple)):
        return tuple(text for item in value if (text := str(item).strip()))
    return ()


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _is_non_evidence_text(value: object) -> bool:
    text = str(value or "").strip()
    return not text or any(marker in text for marker in _NON_EVIDENCE_PHRASES)


def _contains_falsifiable_marker(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and any(
        marker in text for marker in ("失效", "若", "跌破", "低于", "不达")
    )


def _has_supporting_evidence(
    pick: PickResult,
    market_context_lines: tuple[str, ...] = (),
) -> bool:
    """Return whether supplied evidence supports a thesis, without using score."""
    metrics = pick.metrics or {}
    fields = (
        "news_catalyst_lead",
        "news_catalyst_title",
        "cross_market_supporting_evidence",
        "cross_market_evidence_points",
        "cross_market_validation_signals",
        "rule_transmission_evidence",
    )
    if any(
        any(not _is_non_evidence_text(item) for item in _text_items(metrics.get(field)))
        for field in fields
    ):
        return True
    return any(
        str(line)
        .strip()
        .startswith(
            (
                "候选消息:",
                "个股催化:",
                "消息催化:",
                "消息支持:",
                "全局雷达:",
                "传导推演[",
                "传导链:",
                "确认信号:",
            )
        )
        and not _is_non_evidence_text(str(line).split(":", 1)[-1])
        for line in market_context_lines
    )


def _has_supporting_result_evidence(result: DebateResult) -> bool:
    """Check synthesized support evidence before adding a fallback condition."""
    values = (
        *result.real_message_evidence,
        *result.cross_market_evidence,
        *result.rule_transmission_evidence,
        *result.support_points,
    )
    return any(
        str(value).strip() and not _is_non_evidence_text(value) for value in values
    )


def _message_evidence_from_context_line(value: object) -> str:
    text = str(value or "").strip()
    for prefix in (
        "候选消息:",
        "个股催化:",
        "消息催化:",
        "消息支持:",
        "消息压力:",
        "全局雷达:",
        "消息结果:",
    ):
        if text.startswith(prefix):
            message = text[len(prefix) :].strip()
            return "" if _is_non_evidence_text(message) else message
    return ""


def _has_substantive_debate_evidence(result: DebateResult) -> bool:
    if result.data_status != "available":
        return False
    values: list[str] = []
    values.extend(result.real_message_evidence)
    values.extend(result.cross_market_evidence)
    values.extend(result.rule_transmission_evidence)
    for round_data in result.rounds:
        for opinion in round_data.opinions:
            values.extend(opinion.arguments)
            values.extend(opinion.risk_factors)
            values.extend(opinion.opportunity_factors)
    return any(
        (text := str(value).strip()) and not _is_non_evidence_text(text)
        for value in values
    )


def debate_active_roles(result: DebateResult) -> tuple[AgentRole, ...]:
    roles: list[AgentRole] = []
    for role in result.final_vote:
        if role not in roles:
            roles.append(role)
    if not roles:
        for round_data in result.rounds:
            for opinion in round_data.opinions:
                if opinion.role not in roles:
                    roles.append(opinion.role)
    if not roles:
        return ()
    return tuple(
        sorted(
            roles,
            key=lambda role: (
                DEFAULT_AGENT_ROLE_ORDER.index(role)
                if role in DEFAULT_AGENT_ROLE_ORDER
                else len(DEFAULT_AGENT_ROLE_ORDER)
            ),
        )
    )


def _audit_result_quality(result: DebateResult):
    """Run the structural audit without making the debate module import eagerly."""
    from aqsp.briefing.debate_tracker import audit_debate_quality

    return audit_debate_quality(
        result,
        expected_roles=(result.expected_roles or debate_active_roles(result)),
    )


def debate_active_role_labels(
    result: DebateResult,
    *,
    language: str = "zh-CN",
) -> tuple[str, ...]:
    return tuple(
        agent_role_label(role, language) for role in debate_active_roles(result)
    )


def debate_active_role_summary(
    result: DebateResult,
    *,
    language: str = "zh-CN",
    max_labels: int = 5,
) -> str:
    labels = debate_active_role_labels(result, language=language)
    if not labels:
        return ""
    if len(labels) <= max_labels:
        return "、".join(labels)
    return "、".join(labels[:max_labels]) + f" 等 {len(labels)} 个角色"


def _debate_adjustment_label(value: str) -> str:
    clean = str(value).strip().lower()
    return {
        "raise": "偏积极",
        "keep": "暂维持",
        "lower": "偏谨慎",
    }.get(clean, "继续观察")


def format_debate_result(result: DebateResult) -> str:
    """格式化辩论结果为可读文本"""
    from aqsp.briefing.conclusion import (
        build_debate_conclusion_view,
        cross_market_priority_digest,
        debate_evidence_provenance,
    )

    lines = [
        f"# 多 Agent 结论 - {result.symbol} {result.name}",
        "",
        f"- 原始评分: **{result.original_score}** ({result.rating})",
        f"- 最终共识: **{result.final_consensus}**",
        f"- 纸面复核口径: **{_debate_adjustment_label(result.recommended_adjustment)}**",
        "- 结论边界: advisory-only；确定性评分保持不变",
        "",
    ]

    conclusion = build_debate_conclusion_view(
        result,
        language="zh-CN",
        max_role_labels=5,
    )
    cross_market_digest = cross_market_priority_digest(result)

    if (
        result.research_verdict
        or cross_market_digest
        or result.primary_risk_gate
        or result.next_trigger
        or result.historical_context_note
        or (
            conclusion.quality_audit is not None and not conclusion.quality_audit.passed
        )
    ):
        lines.append("## 裁决压缩")
        if conclusion.quality_audit is not None and not conclusion.quality_audit.passed:
            lines.append(f"- 研究口径: {conclusion.headline}")
        elif result.research_verdict:
            lines.append(f"- 研究口径: {conclusion.headline}")
        if cross_market_digest:
            lines.append(f"- 跨市判断: {cross_market_digest}")
        if conclusion.risk_gate_line:
            lines.append(f"- {conclusion.risk_gate_line}")
        if conclusion.trigger_line:
            lines.append(f"- {conclusion.trigger_line}")
        if conclusion.historical_context_line:
            lines.append(f"- {conclusion.historical_context_line}")
        elif result.historical_context_note:
            lines.append(f"- {result.historical_context_note}")
        lines.append("")

    if result.adjustment_reason:
        lines.append(result.adjustment_reason)
        lines.append("")

    if result.market_context_lines:
        lines.append("## 市场上下文")
        for line in result.market_context_lines[:3]:
            lines.append(f"- {line}")
        lines.append("")

    lines.append("## 数据状态")
    if result.data_status == "available":
        lines.append("- 行情数据: 可用")
    else:
        lines.append(f"- 行情数据: 空数据，{result.data_note or '不形成证据结论'}")
    lines.append("")

    provenance = debate_evidence_provenance(result)
    lines.append("## 证据分层")
    lines.append(
        "- 真实消息: "
        + (
            "；".join(provenance.real_messages)
            if provenance.real_messages
            else "无可用消息证据"
        )
    )
    lines.append(
        "- 跨市证据: "
        + (
            "；".join(provenance.cross_market_evidence)
            if provenance.cross_market_evidence
            else "无可用跨市证据"
        )
    )
    lines.append(
        "- 规则传导: "
        + (
            "；".join(provenance.rule_transmissions)
            if provenance.rule_transmissions
            else "无可用规则传导"
        )
    )
    lines.append(
        "- 待确认: "
        + (
            "；".join(provenance.pending_confirmations)
            if provenance.pending_confirmations
            else "等待实时量价与来源恢复后确认"
        )
    )
    lines.append("")

    round_summaries = [
        f"- 第{round_data.round_num}轮: {round_data.summary}"
        for round_data in result.rounds
        if round_data.summary
    ]
    if round_summaries:
        lines.append("## 讨论摘要")
        lines.extend(round_summaries[:3])
        lines.append("")

    if result.support_points:
        lines.append("## 支持观点")
        for line in result.support_points:
            lines.append(f"- {line}")
        lines.append("")

    if result.opposition_points:
        lines.append("## 反对观点")
        for line in result.opposition_points:
            lines.append(f"- {line}")
        lines.append("")

    if result.watch_items:
        lines.append("## 待确认")
        for line in result.watch_items:
            lines.append(f"- {line}")
        lines.append("")

    if (
        conclusion.active_roles_line
        or conclusion.role_selection_line
        or conclusion.role_plan_line
        or result.role_reliability_lines
    ):
        lines.append("## 视角与分工")
        if conclusion.active_roles_line:
            lines.append(f"- {conclusion.active_roles_line}")
        if conclusion.role_selection_line:
            lines.append(f"- {conclusion.role_selection_line}")
        if conclusion.role_plan_line:
            lines.append(f"- {conclusion.role_plan_line}")
        if result.role_reliability_lines:
            lines.append(
                f"- 角色可信度: {'；'.join(result.role_reliability_lines[:2])}"
            )
        lines.append("")

    # 最终投票结果
    lines.append("## 最终投票")
    bullish_count = sum(1 for v in result.final_vote.values() if v == "bullish")
    bearish_count = sum(1 for v in result.final_vote.values() if v == "bearish")
    neutral_count = sum(1 for v in result.final_vote.values() if v == "neutral")

    lines.append(f"- 看多: {bullish_count} 票")
    lines.append(f"- 看空: {bearish_count} 票")
    lines.append(f"- 中性: {neutral_count} 票")
    lines.append("")

    # 只输出结构化角色状态和交锋对象，不输出原始模型话术。
    lines.append("## 各角色状态")
    for opinion in result.final_vote.keys():
        name = agent_role_label(opinion, language="zh-CN")
        stance = result.final_vote[opinion]
        lines.append(f"- {name}: {stance}")

        final_round = result.rounds[-1] if result.rounds else None
        if final_round:
            detail = next(
                (op for op in final_round.opinions if op.role == opinion),
                None,
            )
            if detail:
                targets = tuple(
                    dict.fromkeys(
                        (*detail.counterargument_roles, *detail.peer_reviewed_roles)
                    )
                )
                if targets:
                    lines.append(f"  - 质询/复核对象: {'、'.join(targets[:4])}")

    lines.append("")
    return "\n".join(lines)
