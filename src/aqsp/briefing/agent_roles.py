from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class AgentRole(Enum):
    """A-share debate agent roles."""

    BULL = "bull"
    BEAR = "bear"
    RISK_CONTROL = "risk_control"
    SECTOR_LEADER = "sector_leader"
    POLICY_SENSITIVE = "policy_sensitive"
    MARGIN_TRADING = "margin_trading"
    NORTHBOUND = "northbound"
    RETAIL_MOOD = "retail_mood"


@dataclass(frozen=True)
class AgentRoleProfile:
    role: AgentRole
    zh_label: str
    en_label: str
    zh_description: str
    en_description: str
    zh_focus: str
    en_focus: str
    zh_challenge_style: str
    en_challenge_style: str
    emoji: str


_ROLE_PROFILES: dict[AgentRole, AgentRoleProfile] = {
    AgentRole.BULL: AgentRoleProfile(
        role=AgentRole.BULL,
        zh_label="技术多头",
        en_label="Bull",
        zh_description="技术面多头，关注量价配合和趋势延续",
        en_description="Bull case focused on price-volume trend continuation",
        zh_focus="只从趋势延续、量价共振、强势结构是否还在扩张这三个角度发言。",
        en_focus="Only argue from trend continuation, price-volume confirmation, and structural strength expansion.",
        zh_challenge_style="优先反驳过度悲观和风控过严，但不能无视风险。",
        en_challenge_style="Challenge excessive pessimism and over-tight risk controls without ignoring risk.",
        emoji="🐂",
    ),
    AgentRole.BEAR: AgentRoleProfile(
        role=AgentRole.BEAR,
        zh_label="基本面空头",
        en_label="Bear",
        zh_description="基本面空头，关注估值和业绩压力",
        en_description="Bear case focused on valuation and earnings pressure",
        zh_focus="只从估值透支、业绩兑现压力、预期差回落三个角度发言。",
        en_focus="Only argue from valuation stretch, earnings delivery risk, and expectation mean reversion.",
        zh_challenge_style="优先拆解多头叙事中的证据不足部分。",
        en_challenge_style="Primarily challenge weak evidence inside bullish narratives.",
        emoji="🐻",
    ),
    AgentRole.RISK_CONTROL: AgentRoleProfile(
        role=AgentRole.RISK_CONTROL,
        zh_label="风险控制",
        en_label="Risk Control",
        zh_description="风险控制专家，关注涨跌停、ST、退市风险",
        en_description="Risk control focused on halt, ST and delisting risk",
        zh_focus="只从流动性、不可成交、回撤控制和止损执行难度角度发言。",
        en_focus="Only argue from liquidity, execution failure, drawdown control, and stop-loss feasibility.",
        zh_challenge_style="优先指出不能成交、不能止损、不能复制的风险。",
        en_challenge_style="Prioritize non-executable, non-stoppable, and non-repeatable risks.",
        emoji="🛡️",
    ),
    AgentRole.SECTOR_LEADER: AgentRoleProfile(
        role=AgentRole.SECTOR_LEADER,
        zh_label="板块轮动",
        en_label="Sector Rotation",
        zh_description="板块轮动专家，关注行业热点切换",
        en_description="Sector rotation focused on industry leadership changes",
        zh_focus="只从板块强弱切换、龙头带动性和持续性角度发言。",
        en_focus="Only argue from sector rotation, leadership spillover, and theme persistence.",
        zh_challenge_style="优先质疑孤立个股、缺乏板块共振的机会。",
        en_challenge_style="Prioritize skepticism toward isolated names lacking sector confirmation.",
        emoji="🔄",
    ),
    AgentRole.POLICY_SENSITIVE: AgentRoleProfile(
        role=AgentRole.POLICY_SENSITIVE,
        zh_label="政策分析",
        en_label="Policy Watcher",
        zh_description="政策分析师，关注监管和产业政策",
        en_description="Policy watcher focused on regulation and industry policy",
        zh_focus="只从监管导向、产业催化和政策预期差角度发言。",
        en_focus="Only argue from regulation, industry catalysts, and policy expectation gaps.",
        zh_challenge_style="优先提示政策兑现落空或监管收紧风险。",
        en_challenge_style="Prioritize risks from policy disappointment or regulatory tightening.",
        emoji="📜",
    ),
    AgentRole.MARGIN_TRADING: AgentRoleProfile(
        role=AgentRole.MARGIN_TRADING,
        zh_label="融资融券",
        en_label="Margin Flow",
        zh_description="融资融券专家，关注杠杆资金动向",
        en_description="Leverage watcher focused on margin funding behavior",
        zh_focus="只从杠杆资金拥挤度、融资追涨和踩踏风险角度发言。",
        en_focus="Only argue from leverage crowding, margin chasing, and deleveraging risk.",
        zh_challenge_style="优先提醒高杠杆环境下的脆弱性。",
        en_challenge_style="Prioritize fragility under crowded leverage conditions.",
        emoji="💰",
    ),
    AgentRole.NORTHBOUND: AgentRoleProfile(
        role=AgentRole.NORTHBOUND,
        zh_label="北向资金",
        en_label="Northbound Flow",
        zh_description="北向资金专家，关注外资配置",
        en_description="Northbound flow watcher focused on foreign capital",
        zh_focus="只从外资偏好、增减仓持续性和风格匹配角度发言。",
        en_focus="Only argue from foreign capital preference, flow persistence, and style alignment.",
        zh_challenge_style="优先区分短期交易性流入和持续配置性流入。",
        en_challenge_style="Distinguish tactical inflows from persistent allocation flows.",
        emoji="🌊",
    ),
    AgentRole.RETAIL_MOOD: AgentRoleProfile(
        role=AgentRole.RETAIL_MOOD,
        zh_label="散户情绪",
        en_label="Retail Mood",
        zh_description="散户情绪专家，关注市场情绪指标",
        en_description="Retail mood watcher focused on sentiment extremes",
        zh_focus="只从情绪温度、跟风拥挤度和反身性波动角度发言。",
        en_focus="Only argue from sentiment temperature, crowding, and reflexive volatility.",
        zh_challenge_style="优先识别情绪过热或过冷带来的反向信号。",
        en_challenge_style="Prioritize reversal signals from sentiment overheating or capitulation.",
        emoji="👥",
    ),
}

DEFAULT_AGENT_ROLE_ORDER: tuple[AgentRole, ...] = tuple(_ROLE_PROFILES)
DEFAULT_RUNTIME_AGENT_ROLE_ORDER: tuple[AgentRole, ...] = (
    AgentRole.BULL,
    AgentRole.BEAR,
    AgentRole.RISK_CONTROL,
    AgentRole.SECTOR_LEADER,
    AgentRole.POLICY_SENSITIVE,
    AgentRole.NORTHBOUND,
)
DEFAULT_RUNTIME_AGENT_ROLE_NAMES: tuple[str, ...] = tuple(
    role.value for role in DEFAULT_RUNTIME_AGENT_ROLE_ORDER
)


def _coerce_role(role: AgentRole | str) -> AgentRole | None:
    if isinstance(role, AgentRole):
        return role
    name = str(role).strip().lower()
    if not name:
        return None
    try:
        return AgentRole(name)
    except ValueError:
        return None


def parse_agent_roles(role_names: Iterable[str]) -> tuple[AgentRole, ...]:
    parsed: list[AgentRole] = []
    for raw_name in role_names:
        role = _coerce_role(raw_name)
        if role is not None and role not in parsed:
            parsed.append(role)
    return tuple(parsed) or DEFAULT_AGENT_ROLE_ORDER


def iter_agent_roles(
    roles: Iterable[AgentRole | str] | None = None,
) -> tuple[AgentRole, ...]:
    if roles is None:
        return DEFAULT_AGENT_ROLE_ORDER
    return parse_agent_roles(
        role.value if isinstance(role, AgentRole) else role for role in roles
    )


def agent_role_profile(role: AgentRole | str) -> AgentRoleProfile | None:
    resolved = _coerce_role(role)
    if resolved is None:
        return None
    return _ROLE_PROFILES[resolved]


def agent_role_label(role: AgentRole | str, language: str = "zh-CN") -> str:
    profile = agent_role_profile(role)
    if profile is None:
        return str(role)
    if language.lower().startswith("en"):
        return profile.en_label
    return profile.zh_label


def agent_role_description(role: AgentRole | str, language: str = "zh-CN") -> str:
    profile = agent_role_profile(role)
    if profile is None:
        return str(role)
    if language.lower().startswith("en"):
        return profile.en_description
    return profile.zh_description


def agent_role_emoji(role: AgentRole | str) -> str:
    profile = agent_role_profile(role)
    if profile is None:
        return ""
    return profile.emoji


def agent_role_focus(role: AgentRole | str, language: str = "zh-CN") -> str:
    profile = agent_role_profile(role)
    if profile is None:
        return ""
    if language.lower().startswith("en"):
        return profile.en_focus
    return profile.zh_focus


def agent_role_challenge_style(role: AgentRole | str, language: str = "zh-CN") -> str:
    profile = agent_role_profile(role)
    if profile is None:
        return ""
    if language.lower().startswith("en"):
        return profile.en_challenge_style
    return profile.zh_challenge_style
