from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Literal
from uuid import uuid4

import pandas as pd

from aqsp.core.types import PickResult

logger = logging.getLogger(__name__)


class AgentRole(Enum):
    """A股市场辩论 Agent 的角色类型"""

    BULL = "bull"  # 多头：强调技术面和资金推动
    BEAR = "bear"  # 空头：强调风险和基本面压力
    RISK_CONTROL = "risk_control"  # 风控：涨跌停、ST、退市风险
    SECTOR_LEADER = "sector_leader"  # 板块轮动：关注行业热点切换
    POLICY_SENSITIVE = "policy_sensitive"  # 政策敏感：关注监管动向
    MARGIN_TRADING = "margin_trading"  # 融资融券：关注杠杆资金动向
    NORTHBOUND = "northbound"  # 北向资金：关注外资动向
    RETAIL_MOOD = "retail_mood"  # 散户情绪：关注市场情绪指标


DEFAULT_AGENT_ROLE_ORDER: tuple[AgentRole, ...] = (
    AgentRole.BULL,
    AgentRole.BEAR,
    AgentRole.RISK_CONTROL,
    AgentRole.SECTOR_LEADER,
    AgentRole.POLICY_SENSITIVE,
    AgentRole.MARGIN_TRADING,
    AgentRole.NORTHBOUND,
    AgentRole.RETAIL_MOOD,
)

_ROLE_DESCRIPTIONS_ZH: dict[AgentRole, str] = {
    AgentRole.BULL: "技术面多头，关注量价配合和趋势延续",
    AgentRole.BEAR: "基本面空头，关注估值和业绩压力",
    AgentRole.RISK_CONTROL: "风险控制专家，关注涨跌停、ST、退市风险",
    AgentRole.SECTOR_LEADER: "板块轮动专家，关注行业热点切换",
    AgentRole.POLICY_SENSITIVE: "政策分析师，关注监管和产业政策",
    AgentRole.MARGIN_TRADING: "融资融券专家，关注杠杆资金动向",
    AgentRole.NORTHBOUND: "北向资金专家，关注外资配置",
    AgentRole.RETAIL_MOOD: "散户情绪专家，关注市场情绪指标",
}

_ROLE_DESCRIPTIONS_EN: dict[AgentRole, str] = {
    AgentRole.BULL: "Bull case focused on price-volume trend continuation",
    AgentRole.BEAR: "Bear case focused on valuation and earnings pressure",
    AgentRole.RISK_CONTROL: "Risk control focused on halt, ST and delisting risk",
    AgentRole.SECTOR_LEADER: "Sector rotation focused on industry leadership changes",
    AgentRole.POLICY_SENSITIVE: "Policy watcher focused on regulation and industry policy",
    AgentRole.MARGIN_TRADING: "Leverage watcher focused on margin funding behavior",
    AgentRole.NORTHBOUND: "Northbound flow watcher focused on foreign capital",
    AgentRole.RETAIL_MOOD: "Retail mood watcher focused on sentiment extremes",
}


def parse_agent_roles(role_names: Iterable[str]) -> tuple[AgentRole, ...]:
    parsed: list[AgentRole] = []
    for raw_name in role_names:
        name = str(raw_name).strip().lower()
        if not name:
            continue
        try:
            role = AgentRole(name)
        except ValueError:
            continue
        if role not in parsed:
            parsed.append(role)
    return tuple(parsed) or DEFAULT_AGENT_ROLE_ORDER


def agent_role_description(role: AgentRole, language: str = "zh-CN") -> str:
    if language.lower().startswith("en"):
        return _ROLE_DESCRIPTIONS_EN.get(role, role.value)
    return _ROLE_DESCRIPTIONS_ZH.get(role, role.value)


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
    ):
        self.role = role
        self.agent_id = f"{role.value}_{uuid4().hex[:8]}"
        self.enable_llm = enable_llm
        self.language = language

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

        return AgentOpinion(
            agent_id=self.agent_id,
            role=self.role,
            stance=stance,
            confidence=confidence,
            arguments=arguments,
            risk_factors=risk_factors,
            opportunity_factors=opportunity_factors,
        )

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
    ):
        self.enable_llm = enable_llm
        self.max_rounds = max_rounds
        self.thresholds_version = thresholds_version
        self.regime = regime
        self.data_source = data_source
        self.language = language
        self.roles = roles or DEFAULT_AGENT_ROLE_ORDER
        self.agents = self._create_agents()

        from aqsp.briefing.debate_tracker import DebatePerformanceTracker

        self.tracker = DebatePerformanceTracker()

    def _create_agents(self) -> list[AShareDebateAgent]:
        """创建所有辩论 Agent"""
        return [
            AShareDebateAgent(
                role,
                enable_llm=self.enable_llm,
                language=self.language,
            )
            for role in self.roles
        ]

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

        final_round = self._run_debate_rounds(result, pick, df)

        self._synthesize_result(result, final_round)

        agent_ids = {agent.role: agent.agent_id for agent in self.agents}
        self._calculate_adjustment(result, agent_ids)

        for opinion in final_round.opinions:
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
                if my_prev:
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
                f"，辩论建议上调评分至 {result.adjusted_score:.1f}"
            )
        elif result.adjustment_weight < 0:
            result.adjustment_reason += (
                f"，辩论建议下调评分至 {result.adjusted_score:.1f}"
            )


def format_debate_result(result: DebateResult) -> str:
    """格式化辩论结果为可读文本"""
    role_emojis = {
        AgentRole.BULL: "🐂",
        AgentRole.BEAR: "🐻",
        AgentRole.RISK_CONTROL: "🛡️",
        AgentRole.SECTOR_LEADER: "🔄",
        AgentRole.POLICY_SENSITIVE: "📜",
        AgentRole.MARGIN_TRADING: "💰",
        AgentRole.NORTHBOUND: "🌊",
        AgentRole.RETAIL_MOOD: "👥",
    }

    role_names = {
        AgentRole.BULL: "技术多头",
        AgentRole.BEAR: "基本面空头",
        AgentRole.RISK_CONTROL: "风险控制",
        AgentRole.SECTOR_LEADER: "板块轮动",
        AgentRole.POLICY_SENSITIVE: "政策分析",
        AgentRole.MARGIN_TRADING: "融资融券",
        AgentRole.NORTHBOUND: "北向资金",
        AgentRole.RETAIL_MOOD: "散户情绪",
    }

    lines = [
        f"# 多Agent辩论 - {result.symbol} {result.name}",
        "",
        f"- 原始评分: **{result.original_score}** ({result.rating})",
        f"- 最终共识: **{result.final_consensus}**",
        f"- 建议操作: **{result.recommended_adjustment.upper()}**",
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
        emoji = role_emojis.get(opinion, "")
        name = role_names.get(opinion, opinion.value)
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
