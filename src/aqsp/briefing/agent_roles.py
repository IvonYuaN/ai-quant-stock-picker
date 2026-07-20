from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from aqsp.core.types import PickResult


class AgentRole(Enum):
    """A-share debate agent roles."""

    BULL = "bull"
    BEAR = "bear"
    RISK_CONTROL = "risk_control"
    SECTOR_LEADER = "sector_leader"
    CROSS_MARKET = "cross_market"
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


@dataclass(frozen=True)
class ContextRoleSignals:
    has_cross_market: bool
    has_policy: bool
    has_northbound: bool
    has_margin: bool
    has_retail: bool
    has_sector: bool
    supportive: bool
    conflicted: bool
    has_validation: bool = False
    has_invalidation: bool = False
    has_pressure: bool = False
    cross_market_theme: str = ""


_ROLE_SELECTION_PLAN_LABELS: dict[AgentRole, str] = {
    AgentRole.BULL: "趋势延续和量价共振",
    AgentRole.BEAR: "预期透支和兑现压力",
    AgentRole.RISK_CONTROL: "流动性、止损和不可成交",
    AgentRole.SECTOR_LEADER: "板块共振和龙头扩散",
    AgentRole.CROSS_MARKET: "海外催化到A股映射",
    AgentRole.POLICY_SENSITIVE: "政策催化和兑现节奏",
    AgentRole.MARGIN_TRADING: "杠杆拥挤和踩踏风险",
    AgentRole.NORTHBOUND: "外资流向是否持续",
    AgentRole.RETAIL_MOOD: "情绪拥挤和反身波动",
}

_RULE_DRIVER_LABELS: dict[str, str] = {
    "commercial_space": "商业航天映射",
    "physical_ai": "物理AI映射",
    "us_risk_on": "外盘修复",
    "chip_export_controls": "芯片限制",
    "global_supply_tightening": "供给收缩",
    "geopolitics": "地缘避险",
    "oil_price_shock": "油价冲击",
}

_THEME_RULE_HINTS: tuple[tuple[str, str], ...] = (
    ("海外商业航天催化", "commercial_space"),
    ("海外物理ai叙事升温", "physical_ai"),
    ("外盘风险偏好修复", "us_risk_on"),
    ("海外芯片限制升级", "chip_export_controls"),
    ("海外供给收缩映射", "global_supply_tightening"),
    ("地缘冲突升温", "geopolitics"),
    ("国际油价冲击", "oil_price_shock"),
)

_RULE_FOCUS_ROLE_HINTS: dict[str, tuple[AgentRole, ...]] = {
    "commercial_space": (
        AgentRole.CROSS_MARKET,
        AgentRole.SECTOR_LEADER,
        AgentRole.RETAIL_MOOD,
        AgentRole.BULL,
    ),
    "physical_ai": (
        AgentRole.CROSS_MARKET,
        AgentRole.SECTOR_LEADER,
        AgentRole.BULL,
        AgentRole.POLICY_SENSITIVE,
    ),
    "us_risk_on": (
        AgentRole.CROSS_MARKET,
        AgentRole.NORTHBOUND,
        AgentRole.BULL,
        AgentRole.SECTOR_LEADER,
    ),
    "chip_export_controls": (
        AgentRole.CROSS_MARKET,
        AgentRole.POLICY_SENSITIVE,
        AgentRole.SECTOR_LEADER,
        AgentRole.RISK_CONTROL,
    ),
    "global_supply_tightening": (
        AgentRole.CROSS_MARKET,
        AgentRole.SECTOR_LEADER,
        AgentRole.BULL,
        AgentRole.BEAR,
    ),
    "geopolitics": (
        AgentRole.CROSS_MARKET,
        AgentRole.RISK_CONTROL,
        AgentRole.SECTOR_LEADER,
        AgentRole.POLICY_SENSITIVE,
    ),
    "oil_price_shock": (
        AgentRole.CROSS_MARKET,
        AgentRole.SECTOR_LEADER,
        AgentRole.RISK_CONTROL,
        AgentRole.BEAR,
        AgentRole.BULL,
    ),
}


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
    AgentRole.CROSS_MARKET: AgentRoleProfile(
        role=AgentRole.CROSS_MARKET,
        zh_label="跨市传导",
        en_label="Cross-Market Linkage",
        zh_description="跨市场传导分析师，关注海外事件如何映射到A股主线",
        en_description="Cross-market linkage watcher focused on how offshore events map into A-share leadership",
        zh_focus="只从海外事件、外盘风格、商品或地缘主题如何传到A股板块与个股、能持续多久、靠什么验证这三个角度发言。",
        en_focus="Only argue from offshore-to-A-share transmission, persistence window, and required confirmation signals.",
        zh_challenge_style="优先拆解只有叙事没有板块共振、没有时效优势、没有验证条件的联想。",
        en_challenge_style="Prioritize challenging narratives that lack sector confirmation, timing edge, or validation conditions.",
        emoji="🌐",
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
    AgentRole.CROSS_MARKET,
    AgentRole.POLICY_SENSITIVE,
    AgentRole.MARGIN_TRADING,
    AgentRole.NORTHBOUND,
    AgentRole.RETAIL_MOOD,
)
DEFAULT_RUNTIME_AGENT_ROLE_NAMES: tuple[str, ...] = tuple(
    role.value for role in DEFAULT_RUNTIME_AGENT_ROLE_ORDER
)

# These buckets are evidence lanes, not votes.  A role may contribute to more
# than one lane, while every lane remains visible even when it has no evidence.
VIEWPOINT_BUCKET_ORDER: tuple[str, ...] = (
    "bullish",
    "bearish",
    "event_fundamental",
    "technical",
    "risk_counterevidence",
    "uncertainty",
)

_ROLE_VIEWPOINT_BUCKETS: dict[AgentRole, tuple[str, ...]] = {
    AgentRole.BULL: ("bullish", "technical"),
    AgentRole.BEAR: ("bearish", "event_fundamental"),
    AgentRole.CROSS_MARKET: ("event_fundamental",),
    AgentRole.SECTOR_LEADER: ("event_fundamental", "technical"),
    AgentRole.POLICY_SENSITIVE: ("event_fundamental",),
    AgentRole.NORTHBOUND: ("event_fundamental",),
    AgentRole.RISK_CONTROL: ("risk_counterevidence",),
    AgentRole.MARGIN_TRADING: ("risk_counterevidence",),
    AgentRole.RETAIL_MOOD: ("risk_counterevidence",),
}


def agent_role_viewpoint_buckets(role: AgentRole | str) -> tuple[str, ...]:
    """Return independent evidence lanes owned by a role."""
    resolved = _coerce_role(role)
    return _ROLE_VIEWPOINT_BUCKETS.get(resolved, ()) if resolved else ()


def empty_viewpoint_buckets() -> dict[str, tuple[str, ...]]:
    """Create a stable shape so empty evidence is visible, not silently lost."""
    return {bucket: () for bucket in VIEWPOINT_BUCKET_ORDER}
_CROSS_MARKET_SIGNAL_KEYWORDS = (
    "海外",
    "外盘",
    "美股",
    "英伟达",
    "nvidia",
    "spacex",
    "risk-on",
    "传导推演[",
)
_POLICY_SIGNAL_KEYWORDS = (
    "政策",
    "监管",
    "发改委",
    "工信部",
    "国常会",
    "财政",
    "补贴",
    "牌照",
    "出口管制",
)
_NORTHBOUND_SIGNAL_KEYWORDS = ("北向", "外资")
_MARGIN_SIGNAL_KEYWORDS = ("融资", "两融", "杠杆", "踩踏", "去杠杆")
_RETAIL_SIGNAL_KEYWORDS = (
    "散户情绪",
    "情绪过热",
    "情绪回落",
    "拥挤",
    "连板",
    "打板",
    "跟风",
    "高开低走",
    "一致性",
)
_SECTOR_SIGNAL_KEYWORDS = ("板块", "龙头", "扩散", "共振", "主线", "轮动")
_NEGATIVE_SIGNAL_KEYWORDS = ("偏空", "回落", "承压", "观察为主", "无跟随", "高开低走")


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


def _collect_agent_roles(
    role_names: Iterable[AgentRole | str],
) -> tuple[AgentRole, ...]:
    parsed: list[AgentRole] = []
    for raw_name in role_names:
        role = _coerce_role(raw_name)
        if role is not None and role not in parsed:
            parsed.append(role)
    return tuple(parsed)


def parse_agent_roles(role_names: Iterable[str]) -> tuple[AgentRole, ...]:
    parsed = _collect_agent_roles(role_names)
    return parsed or DEFAULT_AGENT_ROLE_ORDER


def select_runtime_agent_roles(
    base_roles: Iterable[AgentRole | str],
    *,
    extra_roles: Iterable[AgentRole | str] = (),
    focus_roles: Iterable[AgentRole | str] = (),
    disabled_roles: Iterable[AgentRole | str] = (),
) -> tuple[AgentRole, ...]:
    resolved_base = _collect_agent_roles(base_roles) or DEFAULT_RUNTIME_AGENT_ROLE_ORDER
    resolved_extra = _collect_agent_roles(extra_roles)
    requested = {role for role in (*resolved_base, *resolved_extra)}
    ordered_pool = tuple(
        role for role in DEFAULT_RUNTIME_AGENT_ROLE_ORDER if role in requested
    )
    resolved_focus = tuple(
        role for role in _collect_agent_roles(focus_roles) if role in ordered_pool
    )
    resolved_disabled = set(_collect_agent_roles(disabled_roles))
    selected = (
        resolved_focus
        + tuple(role for role in ordered_pool if role not in resolved_focus)
        if resolved_focus
        else ordered_pool
    )
    active = tuple(role for role in selected if role not in resolved_disabled)
    return active or selected


def infer_context_agent_roles(
    pick: PickResult,
    *,
    base_roles: Iterable[AgentRole | str],
    market_context_lines: Iterable[str] = (),
    disabled_roles: Iterable[AgentRole | str] = (),
) -> tuple[AgentRole, ...]:
    resolved_base = _collect_agent_roles(base_roles) or DEFAULT_RUNTIME_AGENT_ROLE_ORDER
    signals = _detect_context_role_signals(
        pick,
        market_context_lines=market_context_lines,
    )
    if not any(
        (
            signals.has_cross_market,
            signals.has_policy,
            signals.has_northbound,
            signals.has_margin,
            signals.has_retail,
            signals.has_sector,
            signals.has_validation,
            signals.has_invalidation,
            signals.has_pressure,
        )
    ):
        return select_runtime_agent_roles(
            resolved_base,
            disabled_roles=disabled_roles,
        )

    rule_ids = _context_rule_ids(pick, market_context_lines=market_context_lines)
    focus_roles: list[AgentRole] = []
    extra_roles: list[AgentRole] = []
    if (
        resolved_base
        and AgentRole.RISK_CONTROL in resolved_base
        and (resolved_base[0] == AgentRole.RISK_CONTROL or not signals.has_cross_market)
    ):
        _append_role(focus_roles, AgentRole.RISK_CONTROL)
    for role in _focus_roles_for_rule_ids(rule_ids):
        _append_role(focus_roles, role)
        _append_role(extra_roles, role)
    if signals.has_cross_market:
        _append_role(focus_roles, AgentRole.CROSS_MARKET)
    if signals.has_sector or signals.has_cross_market:
        _append_role(focus_roles, AgentRole.SECTOR_LEADER)
    if signals.has_policy:
        _append_role(extra_roles, AgentRole.POLICY_SENSITIVE)
        _append_role(focus_roles, AgentRole.POLICY_SENSITIVE)
    if AgentRole.RISK_CONTROL in resolved_base:
        _append_role(focus_roles, AgentRole.RISK_CONTROL)
    if signals.has_northbound:
        _append_role(extra_roles, AgentRole.NORTHBOUND)
        _append_role(focus_roles, AgentRole.NORTHBOUND)
    if signals.has_margin:
        _append_role(extra_roles, AgentRole.MARGIN_TRADING)
        _append_role(focus_roles, AgentRole.MARGIN_TRADING)
    if signals.has_retail:
        _append_role(extra_roles, AgentRole.RETAIL_MOOD)
        _append_role(focus_roles, AgentRole.RETAIL_MOOD)
    if signals.supportive or signals.has_validation:
        _append_role(extra_roles, AgentRole.BULL)
        _append_role(focus_roles, AgentRole.BULL)
    if signals.conflicted or signals.has_invalidation or signals.has_pressure:
        _append_role(extra_roles, AgentRole.BEAR)
        _append_role(focus_roles, AgentRole.BEAR)

    return select_runtime_agent_roles(
        resolved_base,
        extra_roles=extra_roles,
        focus_roles=focus_roles,
        disabled_roles=disabled_roles,
    )


def summarize_context_agent_roles(
    pick: PickResult,
    *,
    selected_roles: Iterable[AgentRole | str],
    market_context_lines: Iterable[str] = (),
    language: str = "zh-CN",
    max_roles: int = 3,
) -> str:
    roles = _collect_agent_roles(selected_roles)
    if not roles:
        return ""
    signals = _detect_context_role_signals(
        pick,
        market_context_lines=market_context_lines,
    )
    drivers: list[str] = []
    primary_rule_label = _primary_rule_driver_label(pick)
    if primary_rule_label:
        drivers.append(primary_rule_label)
    elif signals.cross_market_theme:
        drivers.append(signals.cross_market_theme)
    elif signals.has_cross_market:
        drivers.append("海外传导")
    if signals.has_policy:
        drivers.append("政策催化")
    if signals.has_northbound:
        drivers.append("北向线索")
    if signals.has_margin:
        drivers.append("杠杆拥挤")
    if signals.has_retail:
        drivers.append("情绪波动")
    if signals.has_sector:
        drivers.append("板块共振")
    if signals.has_validation and "确认线索" not in drivers:
        drivers.append("确认线索")
    if signals.has_pressure and "承压切换" not in drivers:
        drivers.append("承压切换")
    if signals.has_invalidation and "失效校验" not in drivers:
        drivers.append("失效校验")
    elif signals.conflicted and "分歧校验" not in drivers:
        drivers.append("分歧校验")
    if signals.supportive and "偏多佐证" not in drivers:
        drivers.append("偏多佐证")
    role_labels = "、".join(
        agent_role_label(role, language=language) for role in roles[:max_roles]
    )
    if not drivers:
        return f"本轮按默认轨道先看 {role_labels}。"
    return f"因{'、'.join(drivers[:3])}，本轮先看 {role_labels}。"


def _context_driver_label(
    pick: PickResult | None,
    *,
    market_context_lines: Iterable[str] = (),
) -> str:
    if pick is None:
        return ""
    primary_rule_label = _primary_rule_driver_label(pick)
    if primary_rule_label:
        return primary_rule_label
    signals = _detect_context_role_signals(
        pick,
        market_context_lines=market_context_lines,
    )
    if signals.cross_market_theme:
        return signals.cross_market_theme
    if signals.has_cross_market:
        return "海外传导"
    return ""


def summarize_context_role_plan(
    *,
    selected_roles: Iterable[AgentRole | str],
    pick: PickResult | None = None,
    market_context_lines: Iterable[str] = (),
    language: str = "zh-CN",
    max_roles: int = 3,
) -> str:
    roles = _collect_agent_roles(selected_roles)
    if not roles:
        return ""
    plans: list[str] = []
    for role in roles[:max_roles]:
        label = agent_role_label(role, language=language)
        task = _ROLE_SELECTION_PLAN_LABELS.get(role, "")
        if label and task:
            plans.append(f"{label}看{task}")
    if not plans:
        return ""
    plan = "；".join(plans) + "。"
    driver_label = _context_driver_label(
        pick,
        market_context_lines=market_context_lines,
    )
    return f"围绕{driver_label}，{plan}" if driver_label else plan


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


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def _append_role(target: list[AgentRole], role: AgentRole) -> None:
    if role not in target:
        target.append(role)


def _context_rule_ids(
    pick: PickResult,
    *,
    market_context_lines: Iterable[str] = (),
) -> tuple[str, ...]:
    metrics = dict(getattr(pick, "metrics", {}) or {})
    explicit = tuple(
        str(item).strip()
        for item in (metrics.get("cross_market_rule_ids") or ())
        if str(item).strip()
    )
    if explicit:
        return explicit
    text_parts = [
        str(metrics.get("cross_market_primary_theme", "") or "").strip(),
        " ".join(
            str(line).strip() for line in market_context_lines if str(line).strip()
        ),
    ]
    inferred: list[str] = []
    haystack = " ".join(part.lower() for part in text_parts if part)
    for needle, rule_id in _THEME_RULE_HINTS:
        if needle.lower() in haystack and rule_id not in inferred:
            inferred.append(rule_id)
    return tuple(inferred)


def _focus_roles_for_rule_ids(rule_ids: Iterable[str]) -> tuple[AgentRole, ...]:
    ordered: list[AgentRole] = []
    for rule_id in rule_ids:
        for role in _RULE_FOCUS_ROLE_HINTS.get(str(rule_id).strip(), ()):
            _append_role(ordered, role)
    return tuple(ordered)


def _primary_rule_driver_label(pick: PickResult) -> str:
    for rule_id in _context_rule_ids(pick):
        label = _RULE_DRIVER_LABELS.get(rule_id)
        if label:
            return label
    return ""


def _detect_context_role_signals(
    pick: PickResult,
    *,
    market_context_lines: Iterable[str] = (),
) -> ContextRoleSignals:
    metrics = dict(getattr(pick, "metrics", {}) or {})
    rule_ids = tuple(
        str(item).strip()
        for item in (metrics.get("cross_market_rule_ids") or ())
        if str(item).strip()
    )
    validation_signals = tuple(
        str(item).strip()
        for item in (metrics.get("cross_market_validation_signals") or ())
        if str(item).strip()
    )
    invalidation_signals = tuple(
        str(item).strip()
        for item in (metrics.get("cross_market_invalidation_signals") or ())
        if str(item).strip()
    )
    pressure_targets = tuple(
        str(item).strip()
        for item in (metrics.get("cross_market_pressure_targets") or ())
        if str(item).strip()
    )
    text_parts = [
        str(getattr(pick, "name", "") or ""),
        " ".join(
            str(item) for item in getattr(pick, "reasons", ()) if str(item).strip()
        ),
        " ".join(str(item) for item in getattr(pick, "risks", ()) if str(item).strip()),
        " ".join(
            str(line).strip() for line in market_context_lines if str(line).strip()
        ),
    ]
    for key in (
        "cross_market_primary_theme",
        "cross_market_linkage_basis",
        "cross_market_action",
        "cross_market_chain_summary",
        "cross_market_evidence_stack_summary",
        "candidate_review_priority",
        "candidate_status",
        "candidate_blocker",
    ):
        value = metrics.get(key)
        if value:
            text_parts.append(str(value))
    text_parts.extend(rule_ids)
    text_parts.extend(validation_signals)
    text_parts.extend(invalidation_signals)
    text_parts.extend(pressure_targets)
    text = " ".join(part for part in text_parts if part).lower()
    support_count = int(metrics.get("cross_market_support_event_count", 0) or 0)
    conflict_count = int(metrics.get("cross_market_conflict_event_count", 0) or 0)
    priority_score = int(metrics.get("cross_market_priority_score", 0) or 0)
    action = str(metrics.get("cross_market_action", "") or "").strip()
    cross_market_theme = str(
        metrics.get("cross_market_primary_theme", "") or ""
    ).strip()
    return ContextRoleSignals(
        has_cross_market=bool(
            cross_market_theme
            or rule_ids
            or priority_score > 0
            or _contains_any(text, _CROSS_MARKET_SIGNAL_KEYWORDS)
        ),
        has_policy=_contains_any(text, _POLICY_SIGNAL_KEYWORDS)
        or any(rule_id == "chip_export_controls" for rule_id in rule_ids),
        has_northbound=_contains_any(text, _NORTHBOUND_SIGNAL_KEYWORDS)
        or any(rule_id == "us_risk_on" for rule_id in rule_ids),
        has_margin=_contains_any(text, _MARGIN_SIGNAL_KEYWORDS),
        has_retail=_contains_any(text, _RETAIL_SIGNAL_KEYWORDS),
        has_sector=_contains_any(text, _SECTOR_SIGNAL_KEYWORDS),
        supportive=(
            support_count > 0
            or action in {"优先复核", "重点跟踪"}
            or bool(validation_signals)
        ),
        conflicted=(
            conflict_count > 0
            or action == "观察为主"
            or bool(invalidation_signals)
            or _contains_any(text, _NEGATIVE_SIGNAL_KEYWORDS)
        ),
        has_validation=bool(validation_signals),
        has_invalidation=bool(invalidation_signals),
        has_pressure=bool(pressure_targets),
        cross_market_theme=cross_market_theme,
    )
