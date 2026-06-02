from __future__ import annotations

from aqsp.briefing.agent_roles import (
    AgentRole,
    DEFAULT_RUNTIME_AGENT_ROLE_NAMES,
    agent_role_challenge_style,
    agent_role_description,
    agent_role_emoji,
    agent_role_focus,
    agent_role_label,
    parse_agent_roles,
)


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
        "policy_sensitive",
        "northbound",
    )
    assert agent_role_label(AgentRole.BULL, language="zh-CN") == "技术多头"
    assert agent_role_label("northbound", language="en-US") == "Northbound Flow"
    assert "监管" in agent_role_description("policy_sensitive", language="zh-CN")
    assert "趋势延续" in agent_role_focus("bull", language="zh-CN")
    assert "不能成交" in agent_role_challenge_style("risk_control", language="zh-CN")
    assert agent_role_emoji("risk_control") == "🛡️"
