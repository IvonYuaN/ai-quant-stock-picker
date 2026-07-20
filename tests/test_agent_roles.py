from __future__ import annotations

from aqsp.briefing.agent_roles import (
    AgentRole,
    DEFAULT_RUNTIME_AGENT_ROLE_NAMES,
    agent_role_challenge_style,
    agent_role_description,
    agent_role_emoji,
    agent_role_focus,
    agent_role_label,
    infer_context_agent_roles,
    parse_agent_roles,
    select_runtime_agent_roles,
    summarize_context_agent_roles,
    summarize_context_role_plan,
    VIEWPOINT_BUCKET_ORDER,
    agent_role_viewpoint_buckets,
    empty_viewpoint_buckets,
)
from aqsp.core.types import PickResult


def test_parse_agent_roles_dedupes_and_ignores_unknown_when_mixed() -> None:
    roles = parse_agent_roles(["bull", "risk_control", "bull", "unknown", "northbound"])

    assert roles == (
        AgentRole.BULL,
        AgentRole.RISK_CONTROL,
        AgentRole.NORTHBOUND,
    )


def test_agent_role_helpers_render_metadata_when_language_switches() -> None:
    assert DEFAULT_RUNTIME_AGENT_ROLE_NAMES == (
        "bull",
        "bear",
        "risk_control",
        "sector_leader",
        "cross_market",
        "policy_sensitive",
        "margin_trading",
        "northbound",
        "retail_mood",
    )
    assert agent_role_label(AgentRole.BULL, language="zh-CN") == "技术多头"
    assert agent_role_label("cross_market", language="zh-CN") == "跨市传导"
    assert agent_role_label("northbound", language="en-US") == "Northbound Flow"
    assert "监管" in agent_role_description("policy_sensitive", language="zh-CN")
    assert "海外事件" in agent_role_focus("cross_market", language="zh-CN")
    assert "趋势延续" in agent_role_focus("bull", language="zh-CN")
    assert "不能成交" in agent_role_challenge_style("risk_control", language="zh-CN")
    assert "板块共振" in agent_role_challenge_style("cross_market", language="zh-CN")
    assert agent_role_emoji("risk_control") == "🛡️"


def test_agent_roles_keep_independent_evidence_lanes_separate() -> None:
    assert agent_role_viewpoint_buckets(AgentRole.BULL) == (
        "bullish",
        "technical",
    )
    assert agent_role_viewpoint_buckets(AgentRole.BEAR) == (
        "bearish",
        "event_fundamental",
    )
    assert agent_role_viewpoint_buckets(AgentRole.CROSS_MARKET) == (
        "event_fundamental",
    )
    assert agent_role_viewpoint_buckets(AgentRole.RISK_CONTROL) == (
        "risk_counterevidence",
    )
    assert tuple(empty_viewpoint_buckets()) == VIEWPOINT_BUCKET_ORDER


def test_select_runtime_agent_roles_focus_reorders_and_disabled_cuts() -> None:
    roles = select_runtime_agent_roles(
        ("bull", "bear", "cross_market", "northbound"),
        focus_roles=("cross_market", "bull", "unknown"),
        disabled_roles=("bull",),
    )

    assert roles == (
        AgentRole.CROSS_MARKET,
        AgentRole.BEAR,
        AgentRole.NORTHBOUND,
    )


def test_select_runtime_agent_roles_preserves_base_when_disabled_would_empty() -> None:
    roles = select_runtime_agent_roles(
        ("bull", "cross_market"),
        disabled_roles=("bull", "cross_market"),
    )

    assert roles == (AgentRole.BULL, AgentRole.CROSS_MARKET)


def test_select_runtime_agent_roles_allows_extra_roles_to_join_runtime_pool() -> None:
    roles = select_runtime_agent_roles(
        ("risk_control", "cross_market"),
        extra_roles=("policy_sensitive",),
        focus_roles=("risk_control", "policy_sensitive", "cross_market"),
    )

    assert roles == (
        AgentRole.RISK_CONTROL,
        AgentRole.POLICY_SENSITIVE,
        AgentRole.CROSS_MARKET,
    )


def test_select_runtime_agent_roles_keeps_non_focus_committee_after_priority_roles() -> (
    None
):
    roles = select_runtime_agent_roles(
        ("bull", "bear", "risk_control", "cross_market", "northbound"),
        focus_roles=("cross_market", "risk_control"),
    )

    assert roles == (
        AgentRole.CROSS_MARKET,
        AgentRole.RISK_CONTROL,
        AgentRole.BULL,
        AgentRole.BEAR,
        AgentRole.NORTHBOUND,
    )


def test_infer_context_agent_roles_focuses_cross_market_policy_and_flows() -> None:
    pick = PickResult(
        symbol="300750",
        name="宁德时代",
        date="2026-07-03",
        close=430.0,
        score=82.0,
        rating="buy_candidate",
        entry_type="next_open",
        ideal_buy=430.0,
        stop_loss=418.0,
        take_profit=455.0,
        position="watch",
        metrics={
            "cross_market_primary_theme": "海外物理AI叙事升温",
            "cross_market_action": "优先复核",
            "cross_market_priority_score": 3,
            "cross_market_support_event_count": 2,
            "cross_market_conflict_event_count": 1,
        },
    )

    roles = infer_context_agent_roles(
        pick,
        base_roles=("risk_control", "sector_leader", "cross_market", "northbound"),
        market_context_lines=(
            "传导推演[海外物理AI叙事升温]: 动作 优先复核。",
            "北向资金: 偏强（5日 z=1.20），外资风险偏好改善。",
            "政策跟踪: 工信部继续强调机器人产业链支持。",
        ),
    )

    assert roles == (
        AgentRole.RISK_CONTROL,
        AgentRole.CROSS_MARKET,
        AgentRole.SECTOR_LEADER,
        AgentRole.BULL,
        AgentRole.POLICY_SENSITIVE,
        AgentRole.NORTHBOUND,
        AgentRole.BEAR,
    )


def test_infer_context_agent_roles_keeps_base_roles_when_no_event_signal() -> None:
    pick = PickResult(
        symbol="600000",
        name="浦发银行",
        date="2026-07-03",
        close=10.0,
        score=50.0,
        rating="watch",
        entry_type="next_open",
        ideal_buy=10.0,
        stop_loss=9.7,
        take_profit=10.6,
        position="watch",
    )

    roles = infer_context_agent_roles(
        pick,
        base_roles=("bull", "bear", "risk_control"),
        market_context_lines=("全局雷达: 无新增跨市线索。",),
    )

    assert roles == (
        AgentRole.BULL,
        AgentRole.BEAR,
        AgentRole.RISK_CONTROL,
    )


def test_summarize_context_agent_roles_explains_why_cross_market_committee_is_focused() -> (
    None
):
    pick = PickResult(
        symbol="300750",
        name="宁德时代",
        date="2026-07-03",
        close=430.0,
        score=82.0,
        rating="buy_candidate",
        entry_type="next_open",
        ideal_buy=430.0,
        stop_loss=418.0,
        take_profit=455.0,
        position="watch",
        metrics={
            "cross_market_primary_theme": "海外物理AI叙事升温",
            "cross_market_action": "优先复核",
            "cross_market_priority_score": 3,
            "cross_market_support_event_count": 2,
            "cross_market_conflict_event_count": 1,
        },
    )

    summary = summarize_context_agent_roles(
        pick,
        selected_roles=(
            AgentRole.CROSS_MARKET,
            AgentRole.SECTOR_LEADER,
            AgentRole.POLICY_SENSITIVE,
        ),
        market_context_lines=(
            "传导推演[海外物理AI叙事升温]: 动作 优先复核。",
            "政策跟踪: 工信部继续强调机器人产业链支持。",
            "北向资金: 偏强（5日 z=1.20），外资风险偏好改善。",
        ),
    )

    assert (
        summary
        == "因物理AI映射、政策催化、北向线索，本轮先看 跨市传导、板块轮动、政策分析。"
    )


def test_infer_context_agent_roles_does_not_auto_add_northbound_without_flow_signal() -> (
    None
):
    pick = PickResult(
        symbol="688012",
        name="中微公司",
        date="2026-07-03",
        close=165.0,
        score=79.0,
        rating="buy_candidate",
        entry_type="next_open",
        ideal_buy=165.0,
        stop_loss=155.0,
        take_profit=182.0,
        position="watch",
        metrics={
            "cross_market_primary_theme": "海外芯片限制升级",
            "cross_market_action": "重点跟踪",
            "cross_market_priority_score": 2,
            "cross_market_rule_ids": ("chip_export_controls",),
            "cross_market_pressure_targets": ("苹果链", "出口代工"),
            "cross_market_validation_signals": (
                "半导体设备材料与国产算力同步放量而非单点脉冲",
            ),
            "cross_market_invalidation_signals": (
                "只有消息刺激但半导体设备材料不扩散",
            ),
        },
    )

    roles = infer_context_agent_roles(
        pick,
        base_roles=("risk_control", "sector_leader", "cross_market"),
        market_context_lines=("传导推演[海外芯片限制升级]: 动作 重点跟踪。",),
    )

    assert roles == (
        AgentRole.RISK_CONTROL,
        AgentRole.CROSS_MARKET,
        AgentRole.POLICY_SENSITIVE,
        AgentRole.SECTOR_LEADER,
        AgentRole.BULL,
        AgentRole.BEAR,
    )


def test_infer_context_agent_roles_uses_explicit_rule_id_as_cross_market_signal() -> (
    None
):
    pick = PickResult(
        symbol="600938",
        name="中国海油",
        date="2026-07-03",
        close=30.0,
        score=68.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=30.0,
        stop_loss=28.8,
        take_profit=33.0,
        position="watch",
        metrics={"cross_market_rule_ids": ("oil_price_shock",)},
    )

    roles = infer_context_agent_roles(
        pick,
        base_roles=("bull", "bear", "risk_control", "cross_market"),
    )

    assert roles[0] is AgentRole.CROSS_MARKET
    assert AgentRole.CROSS_MARKET in roles


def test_infer_context_agent_roles_promotes_sector_committee_when_only_sector_signal() -> (
    None
):
    pick = PickResult(
        symbol="002560",
        name="通达股份",
        date="2026-07-03",
        close=9.8,
        score=67.0,
        rating="watch",
        entry_type="next_open",
        ideal_buy=9.8,
        stop_loss=9.2,
        take_profit=10.7,
        position="watch",
        metrics={"candidate_status": "板块共振扩散"},
    )

    roles = infer_context_agent_roles(
        pick,
        base_roles=("bull", "bear", "risk_control", "sector_leader"),
        market_context_lines=("板块共振增强，龙头继续扩散。",),
    )

    assert roles == (
        AgentRole.RISK_CONTROL,
        AgentRole.SECTOR_LEADER,
        AgentRole.BULL,
        AgentRole.BEAR,
    )


def test_summarize_context_role_plan_briefs_each_selected_role() -> None:
    pick = PickResult(
        symbol="300750",
        name="宁德时代",
        date="2026-07-03",
        close=430.0,
        score=82.0,
        rating="buy_candidate",
        entry_type="next_open",
        ideal_buy=430.0,
        stop_loss=418.0,
        take_profit=455.0,
        position="watch",
        metrics={
            "cross_market_primary_theme": "海外物理AI叙事升温",
            "cross_market_rule_ids": ("physical_ai",),
        },
    )
    plan = summarize_context_role_plan(
        pick=pick,
        selected_roles=(
            AgentRole.CROSS_MARKET,
            AgentRole.SECTOR_LEADER,
            AgentRole.POLICY_SENSITIVE,
        ),
        market_context_lines=("传导推演[海外物理AI叙事升温]: 动作 优先复核。",),
    )

    assert (
        plan
        == "围绕物理AI映射，跨市传导看海外催化到A股映射；板块轮动看板块共振和龙头扩散；政策分析看政策催化和兑现节奏。"
    )


def test_summarize_context_role_plan_keeps_plain_plan_without_theme_driver() -> None:
    plan = summarize_context_role_plan(
        selected_roles=(AgentRole.BULL, AgentRole.RISK_CONTROL),
    )

    assert plan == "技术多头看趋势延续和量价共振；风险控制看流动性、止损和不可成交。"


def test_summarize_context_role_plan_briefs_oil_price_shock_driver() -> None:
    pick = PickResult(
        symbol="600938",
        name="中国海油",
        date="2026-07-10",
        close=30.0,
        score=68.0,
        rating="watch",
        entry_type="relative_strength",
        ideal_buy=30.0,
        stop_loss=28.8,
        take_profit=33.0,
        position="watch",
        metrics={"cross_market_rule_ids": ("oil_price_shock",)},
    )

    plan = summarize_context_role_plan(
        pick=pick,
        selected_roles=(
            AgentRole.CROSS_MARKET,
            AgentRole.SECTOR_LEADER,
            AgentRole.RISK_CONTROL,
            AgentRole.BEAR,
        ),
        market_context_lines=("传导推演[国际油价冲击]: 动作 优先复核。",),
        max_roles=4,
    )

    assert (
        plan
        == "围绕油价冲击，跨市传导看海外催化到A股映射；板块轮动看板块共振和龙头扩散；风险控制看流动性、止损和不可成交；基本面空头看预期透支和兑现压力。"
    )
