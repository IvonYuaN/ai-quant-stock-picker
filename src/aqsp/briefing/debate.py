from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

import pandas as pd

from aqsp.config import DebateRoleRuntime
from aqsp.briefing.agent_roles import (
    AgentRole,
    DEFAULT_AGENT_ROLE_ORDER,
    agent_role_challenge_style,
    agent_role_description,
    agent_role_emoji,
    agent_role_focus,
    agent_role_label,
    parse_agent_roles as _parse_agent_roles,
)
from aqsp.core.types import PickResult
from aqsp.utils.llm_safe import llm_call_or_fallback

logger = logging.getLogger(__name__)


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
    risk_factors: list[str] = field(default_factory=list)  # 风险因素
    opportunity_factors: list[str] = field(default_factory=list)  # 机会因素
    final_position: Literal["bullish", "bearish", "neutral"] | None = None


@dataclass
class DebateRound:
    """辩论的一轮"""

    round_num: int
    opinions: list[AgentOpinion]
    summary: str = ""
    cross_opinions: dict[str, list[str]] = field(default_factory=dict)  # 跨角色观点


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

    # 溯源信息
    thresholds_version: str = ""
    regime: str = ""
    data_source: str = ""
    related_signal_date: str = ""

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

    # 风险与机会
    risk_warnings: list[str] = field(default_factory=list)
    opportunity_highlights: list[str] = field(default_factory=list)

    # Agent表现快照（辩论时的权重计算依据）
    agent_performance_snapshot: dict[str, AgentPerformanceMetrics] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict:
        """转换为可序列化的字典"""
        return {
            "debate_id": self.debate_id,
            "symbol": self.symbol,
            "name": self.name,
            "original_score": self.original_score,
            "rating": self.rating,
            "thresholds_version": self.thresholds_version,
            "regime": self.regime,
            "data_source": self.data_source,
            "related_signal_date": self.related_signal_date,
            "disagreement_score": self.disagreement_score,
            "adjustment_weight": self.adjustment_weight,
            "adjusted_score": self.adjusted_score,
            "recommended_adjustment": self.recommended_adjustment,
            "adjustment_reason": self.adjustment_reason,
            "final_consensus": self.final_consensus,
            "final_vote": {k.value: v for k, v in self.final_vote.items()},
            "risk_warnings": self.risk_warnings,
            "opportunity_highlights": self.opportunity_highlights,
            "agent_performance_snapshot": {
                k: v.to_dict() for k, v in self.agent_performance_snapshot.items()
            },
        }


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
        self.agent_id = f"{role.value}_{uuid4().hex[:8]}"
        self.enable_llm = enable_llm
        self.language = language
        self.llm_provider = llm_provider.strip().lower()
        self.llm_model = llm_model.strip()

    def get_role_description(self) -> str:
        """获取角色描述"""
        return agent_role_description(self.role, self.language)

    def generate_initial_opinion(
        self,
        pick: PickResult,
        df: pd.DataFrame,
    ) -> AgentOpinion:
        """生成初始观点"""
        stance = self._determine_stance(pick)
        confidence = self._calculate_confidence(pick, df)
        arguments = self._build_arguments(pick, df, stance)
        risk_factors = self._identify_risk_factors(pick, df)
        opportunity_factors = self._identify_opportunity_factors(pick, df)

        opinion = AgentOpinion(
            agent_id=self.agent_id,
            role=self.role,
            stance=stance,
            confidence=confidence,
            arguments=arguments,
            risk_factors=risk_factors,
            opportunity_factors=opportunity_factors,
        )
        return self._maybe_enhance_initial_opinion(opinion, pick)

    def _maybe_enhance_initial_opinion(
        self,
        opinion: AgentOpinion,
        pick: PickResult,
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
                prompt=self._build_initial_prompt(pick, opinion),
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
        return AgentOpinion(
            agent_id=opinion.agent_id,
            role=opinion.role,
            stance=opinion.stance,
            confidence=opinion.confidence,
            arguments=self._normalize_points(
                payload.get("arguments"), opinion.arguments
            ),
            counterarguments=opinion.counterarguments.copy(),
            risk_factors=self._normalize_points(
                payload.get("risk_factors"),
                opinion.risk_factors,
            ),
            opportunity_factors=self._normalize_points(
                payload.get("opportunity_factors"),
                opinion.opportunity_factors,
            ),
            final_position=opinion.final_position,
        )

    def _build_initial_prompt(self, pick: PickResult, opinion: AgentOpinion) -> str:
        return f"""
你是 A 股多 Agent 辩论系统中的一个固定角色。

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
    ) -> Literal["bullish", "bearish", "neutral"]:
        """确定立场"""
        if self.role == AgentRole.BULL:
            return "bullish" if pick.score > 50 else "neutral"
        elif self.role == AgentRole.BEAR:
            return "bearish" if pick.score < 50 else "neutral"
        elif self.role == AgentRole.RISK_CONTROL:
            # 风控更保守
            if "ST" in pick.name or "ST" in str(pick.risks):
                return "bearish"
            return "bearish" if pick.score < 60 else "neutral"
        elif self.role == AgentRole.SECTOR_LEADER:
            # 板块专家关注热点轮动
            return "neutral"
        elif self.role == AgentRole.POLICY_SENSITIVE:
            # 政策敏感型关注监管动向
            return "neutral"
        elif self.role == AgentRole.MARGIN_TRADING:
            # 融资专家保持中立
            return "neutral"
        elif self.role == AgentRole.NORTHBOUND:
            # 北向资金专家关注外资
            return "neutral"
        elif self.role == AgentRole.RETAIL_MOOD:
            # 散户情绪专家
            return "neutral"
        return "neutral"

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
    ) -> list[str]:
        """构建论据"""
        args = []

        if self.role == AgentRole.BULL:
            if stance == "bullish":
                args.append("技术面强势，趋势延续概率大")
                if "突破" in str(pick.reasons) or "放量" in str(pick.reasons):
                    args.append("量价配合良好，资金入场积极")
                args.append("均线系统多头排列")
            else:
                args.append("技术面需进一步确认")
        elif self.role == AgentRole.BEAR:
            if stance == "bearish":
                args.append("估值压力较大，安全边际不足")
                if pick.score < 40:
                    args.append("技术形态偏弱，反弹力度有限")
                args.append("基本面支撑不足")
            else:
                args.append("估值已在合理区间")
        elif self.role == AgentRole.RISK_CONTROL:
            args.append(self._analyze_risk_factors(pick))
        elif self.role == AgentRole.SECTOR_LEADER:
            args.append("需关注板块轮动节奏")
            args.append("当前热点是否持续是关键")
        elif self.role == AgentRole.POLICY_SENSITIVE:
            args.append("政策面对行业影响需持续关注")
            args.append("监管动态可能影响短期走势")
        elif self.role == AgentRole.MARGIN_TRADING:
            args.append("融资余额变化反映杠杆资金态度")
            args.append("警惕融资盘踩踏风险")
        elif self.role == AgentRole.NORTHBOUND:
            args.append("北向资金持续净流入是积极信号")
            args.append("外资配置偏好需关注")
        elif self.role == AgentRole.RETAIL_MOOD:
            args.append("散户情绪指标显示市场热度")
            args.append("警惕情绪过热导致的回调风险")

        return args

    def _analyze_risk_factors(self, pick: PickResult) -> str:
        """分析风险因素"""
        risks = []

        if "ST" in pick.name or "st" in pick.rating.lower():
            risks.append("ST股风险")
        if "涨停" in str(pick.risks) or "涨停" in str(pick.reasons):
            risks.append("涨停板流动性风险")
        if pick.score > 80:
            risks.append("高分股回调风险")

        if risks:
            return f"风险提示: {', '.join(risks)}"
        return "未发现明显风险因素"

    def _identify_risk_factors(
        self,
        pick: PickResult,
        df: pd.DataFrame,
    ) -> list[str]:
        """识别风险因素"""
        risks = []

        if pick.risks:
            risks.extend([f"策略提示: {r}" for r in pick.risks])

        if self.role == AgentRole.RISK_CONTROL:
            if "ST" in pick.name:
                risks.append("⚠️ ST股：存在退市风险")
            if pick.score > 80:
                risks.append("⚠️ 高分股：警惕回调风险")
            if not df.empty and len(df) >= 5:
                latest = df.iloc[-1]
                prev = df.iloc[-2]
                if latest["close"] > prev["close"] * 1.09:
                    risks.append("⚠️ 接近涨停：流动性风险")
        elif self.role == AgentRole.BEAR:
            if pick.score < 50:
                risks.append("⚠️ 技术面偏弱")
            if pick.score < 40:
                risks.append("⚠️ 基本面支撑不足")
        elif self.role == AgentRole.SECTOR_LEADER:
            risks.append("⚠️ 板块轮动可能导致风格切换")
        elif self.role == AgentRole.POLICY_SENSITIVE:
            risks.append("⚠️ 政策变动可能影响短期走势")
        elif self.role == AgentRole.MARGIN_TRADING:
            risks.append("⚠️ 融资盘波动放大风险")
        elif self.role == AgentRole.NORTHBOUND:
            risks.append("⚠️ 外资流向存在不确定性")
        elif self.role == AgentRole.RETAIL_MOOD:
            risks.append("⚠️ 散户情绪过热时需谨慎")

        return risks[:3]  # 最多返回3个风险因素

    def _identify_opportunity_factors(
        self,
        pick: PickResult,
        df: pd.DataFrame,
    ) -> list[str]:
        """识别机会因素"""
        opportunities = []

        if pick.strategies:
            opportunities.append(f"命中策略: {', '.join(pick.strategies)}")

        if self.role == AgentRole.BULL:
            if pick.score > 60:
                opportunities.append("✅ 技术面强势")
                if "突破" in str(pick.reasons):
                    opportunities.append("✅ 突破关键阻力位")
            if not df.empty and len(df) >= 10:
                recent_trend = df.tail(10)["close"].values
                if all(
                    recent_trend[i] <= recent_trend[i + 1]
                    for i in range(len(recent_trend) - 1)
                ):
                    opportunities.append("✅ 10日连续上涨趋势")
        elif self.role == AgentRole.SECTOR_LEADER:
            opportunities.append("✅ 行业热点轮动机会")
        elif self.role == AgentRole.POLICY_SENSITIVE:
            opportunities.append("✅ 政策支持行业具有超额收益")
        elif self.role == AgentRole.MARGIN_TRADING:
            opportunities.append("✅ 融资资金流入显示信心")
        elif self.role == AgentRole.NORTHBOUND:
            opportunities.append("✅ 外资持续流入是积极信号")
        elif self.role == AgentRole.RETAIL_MOOD:
            opportunities.append("✅ 市场情绪高涨利于多头")

        return opportunities[:3]  # 最多返回3个机会因素

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
        )

        counterargs = []
        for other in others_opinions:
            if other.role == self.role or other.stance == my_opinion.stance:
                continue

            # 基于角色生成针对性质疑
            if self._should_counter(other, updated_opinion):
                counterargs.append(
                    self._generate_counterargument(other, updated_opinion)
                )

        updated_opinion.counterarguments.extend(counterargs)

        # 如果反对意见太多，降低信心
        if len(counterargs) > 2:
            updated_opinion.confidence *= 0.85

        return updated_opinion

    def _should_counter(
        self,
        other: AgentOpinion,
        my_opinion: AgentOpinion,
    ) -> bool:
        """判断是否应该反驳"""
        if other.stance == "neutral":
            return False
        if other.role == AgentRole.RISK_CONTROL and my_opinion.stance == "bullish":
            return True
        if other.role == AgentRole.BEAR and my_opinion.stance == "bullish":
            return True
        if other.role == AgentRole.BULL and my_opinion.stance == "bearish":
            return True
        return abs(my_opinion.confidence - other.confidence) < 0.2

    def _generate_counterargument(
        self,
        other: AgentOpinion,
        my_opinion: AgentOpinion,
    ) -> str:
        """生成针对性质疑"""
        if self.role == AgentRole.BULL:
            if other.role == AgentRole.BEAR:
                return "【反驳Bear】短期波动不改中期趋势"
            elif other.role == AgentRole.RISK_CONTROL:
                return "【反驳风控】风险已在评分中体现"
        elif self.role == AgentRole.BEAR:
            if other.role == AgentRole.BULL:
                return "【反驳Bull】基本面未好转前需谨慎"
        elif self.role == AgentRole.RISK_CONTROL:
            if other.role == AgentRole.BULL:
                return "【风控提醒】需设置更严格的止损位"
        elif self.role == AgentRole.SECTOR_LEADER:
            if other.role == AgentRole.BULL:
                return "【板块提醒】热点切换可能影响持续性"
        elif self.role == AgentRole.POLICY_SENSITIVE:
            if other.role == AgentRole.BULL:
                return "【政策提醒】需持续关注监管动态"
        elif self.role == AgentRole.MARGIN_TRADING:
            if other.role == AgentRole.BULL:
                return "【融资提醒】杠杆资金动向需关注"
        elif self.role == AgentRole.NORTHBOUND:
            if other.role == AgentRole.BULL:
                return "【外资提醒】北向资金持续性待观察"
        elif self.role == AgentRole.RETAIL_MOOD:
            if other.role == AgentRole.BULL:
                return "【情绪提醒】警惕情绪过热后的回调"

        return f"【{other.role.value}】{other.arguments[0] if other.arguments else '观点需进一步验证'}"


class AShareDebateCoordinator:
    """A股市场辩论协调器 - 管理多Agent辩论流程"""

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
    ):
        self.enable_llm = enable_llm
        self.max_rounds = max_rounds
        self.thresholds_version = thresholds_version
        self.regime = regime
        self.data_source = data_source
        self.language = language
        self.roles = roles or DEFAULT_AGENT_ROLE_ORDER
        self.role_runtime = {item.role: item for item in (role_runtime or ())}
        self.agents = self._create_agents()

        from aqsp.briefing.debate_tracker import DebatePerformanceTracker

        self.tracker = DebatePerformanceTracker()

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
    ) -> DebateResult:
        """运行完整辩论流程"""
        from aqsp.core.time import now_shanghai

        result = DebateResult(
            debate_id=uuid4().hex,
            symbol=pick.symbol,
            name=pick.name,
            original_score=pick.score,
            rating=pick.rating,
            thresholds_version=self.thresholds_version,
            regime=self.regime,
            data_source=self.data_source,
            related_signal_date=signal_date or now_shanghai().date().isoformat(),
        )

        final_opinions = self._run_debate_rounds(result, pick, df)

        self._synthesize_result(result, final_opinions)

        agent_ids = {agent.role: agent.agent_id for agent in self.agents}
        self._calculate_adjustment(result, agent_ids)

        for opinion in final_opinions:
            result.agent_performance_snapshot[opinion.role.value] = (
                self.tracker.get_agent_metrics(opinion.role, opinion.agent_id)
            )

        return result

    def _run_debate_rounds(
        self,
        result: DebateResult,
        pick: PickResult,
        df: pd.DataFrame,
    ) -> list[AgentOpinion]:
        """运行辩论轮次"""
        round1_opinions = []
        for agent in self.agents:
            opinion = agent.generate_initial_opinion(pick, df)
            round1_opinions.append(opinion)

        result.rounds.append(
            DebateRound(
                round_num=1,
                opinions=round1_opinions,
            )
        )

        for round_num in range(2, self.max_rounds + 1):
            prev_round = result.rounds[-1]
            current_opinions = []

            for agent in self.agents:
                my_prev = next(
                    (op for op in prev_round.opinions if op.agent_id == agent.agent_id),
                    None,
                )
                if my_prev is None:
                    logger.error(
                        f"辩论链路断裂: Agent {agent.agent_id} 缺失第 {round_num - 1} 轮观点，无法继续辩论"
                    )
                    raise ValueError(f"Agent {agent.agent_id} 的观点缺失，辩论中止")
                updated = agent.respond_to_counterarguments(
                    my_prev,
                    prev_round.opinions,
                )
                current_opinions.append(updated)

            result.rounds.append(
                DebateRound(
                    round_num=round_num,
                    opinions=current_opinions,
                )
            )

        return result.rounds[-1].opinions

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
        consensus_positions = [
            pos for pos, count in vote_counts.items() if count == max_vote
        ]
        result.final_consensus = (
            consensus_positions[0] if len(consensus_positions) == 1 else "split"
        )

        result.adjustment_reason = (
            f"多头{vote_counts['bullish']}票 vs 空头{vote_counts['bearish']}票"
        )

    def _calculate_adjustment(
        self,
        result: DebateResult,
        agent_ids: dict[AgentRole, str],
    ) -> None:
        """计算评分调整"""
        votes = {role: stance for role, stance in result.final_vote.items()}
        agent_weights = self.tracker.get_all_weights(agent_ids, regime=self.regime)

        (
            adjustment_weight,
            disagreement_score,
            recommended_adjustment,
        ) = self.tracker.calculate_debate_adjustment(votes, agent_weights)

        result.disagreement_score = disagreement_score
        result.adjustment_weight = adjustment_weight
        result.adjusted_score = result.original_score * (1 + adjustment_weight)
        result.recommended_adjustment = recommended_adjustment

        if result.adjustment_weight > 0:
            result.adjustment_reason += (
                f"，辩论倾向上调；附件参考分 {result.adjusted_score:.1f}，不改写系统评分"
            )
        elif result.adjustment_weight < 0:
            result.adjustment_reason += (
                f"，辩论倾向下调；附件参考分 {result.adjusted_score:.1f}，不改写系统评分"
            )


def format_debate_result(result: DebateResult) -> str:
    """格式化辩论结果为可读文本"""
    lines = [
        f"# 多Agent辩论 - {result.symbol} {result.name}",
        "",
        f"- 原始评分: **{result.original_score}** ({result.rating})",
        f"- 最终共识: **{result.final_consensus}**",
        f"- 纸面复核口径: **{result.recommended_adjustment.upper()}**",
        "",
    ]

    if result.adjustment_reason:
        lines.append(result.adjustment_reason)
        lines.append("")

    # 最终投票结果
    lines.append("## 最终投票")
    bullish_count = sum(1 for v in result.final_vote.values() if v == "bullish")
    bearish_count = sum(1 for v in result.final_vote.values() if v == "bearish")
    neutral_count = sum(1 for v in result.final_vote.values() if v == "neutral")

    lines.append(f"- 🐂 看多: {bullish_count} 票")
    lines.append(f"- 🐻 看空: {bearish_count} 票")
    lines.append(f"- ⚖️ 中性: {neutral_count} 票")
    lines.append("")

    # 各 Agent 观点
    lines.append("## 各Agent观点")
    for opinion in result.final_vote.keys():
        emoji = agent_role_emoji(opinion)
        name = agent_role_label(opinion, language="zh-CN")
        stance = result.final_vote[opinion]
        stance_emoji = {"bullish": "🐂", "bearish": "🐻", "neutral": "⚖️"}.get(
            stance, ""
        )

        lines.append(f"\n### {emoji} {name} {stance_emoji}")

        # 找对应的观点详情
        final_round = result.rounds[-1] if result.rounds else None
        if final_round:
            detail = next(
                (op for op in final_round.opinions if op.role == opinion),
                None,
            )
            if detail:
                lines.append(f"**立场**: {stance}")
                if detail.risk_factors:
                    lines.append("**风险因素**:")
                    for rf in detail.risk_factors[:2]:
                        lines.append(f"- {rf}")
                if detail.opportunity_factors:
                    lines.append("**机会因素**:")
                    for of in detail.opportunity_factors[:2]:
                        lines.append(f"- {of}")
                if detail.counterarguments:
                    lines.append("**反驳意见**:")
                    for ca in detail.counterarguments[:2]:
                        lines.append(f"- {ca}")

    lines.append("")
    return "\n".join(lines)
