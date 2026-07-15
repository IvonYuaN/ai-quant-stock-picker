from __future__ import annotations

from aqsp.goal_switches import (
    goal_switch_enabled,
    goal_switch_runtime_summary,
    goal_switch_visibility_notes,
    load_goal_switches,
)


def test_load_goal_switches_reads_default_matrix() -> None:
    matrix = load_goal_switches()

    assert matrix.version == "2026-06-30"
    assert matrix.mode == "short_term_realtime"
    assert matrix.principle_enabled("realtime_priority") is True
    assert matrix.switch_enabled("multi_agent_advisory_layer") is True
    assert matrix.switch("market_intelligence_fusion") is None
    assert matrix.tracks[0].track_id == "realtime_data_guardrails"
    assert matrix.tracks[0].label == "实时数据守卫"
    assert tuple(item.track_id for item in matrix.prioritized_tracks(limit=3)) == (
        "historical_validation_boundary",
        "realtime_data_guardrails",
        "market_intelligence_fusion",
    )


def test_goal_switch_enabled_respects_env_override(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_GOAL_SWITCH_MULTI_AGENT_ADVISORY_LAYER", "false")

    assert goal_switch_enabled("multi_agent_advisory_layer", default=True) is False


def test_goal_switch_runtime_summary_and_visibility_notes_reflect_matrix(
    monkeypatch, tmp_path
) -> None:
    goal_switch_path = tmp_path / "goal_switches.yaml"
    goal_switch_path.write_text(
        """
version: "test"
mode: short_term_realtime
switches:
  historical_validation_only:
    enabled: true
    purpose: history only
  realtime_fallback_chain:
    enabled: false
    purpose: fallback disabled
  domestic_market_intelligence:
    enabled: false
    purpose: domestic disabled
  global_market_intelligence:
    enabled: true
    purpose: global enabled
  pit_enrichment_runtime_required:
    enabled: true
    purpose: pit required
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_GOAL_SWITCHES", str(goal_switch_path))

    assert goal_switch_runtime_summary() == (
        "运行边界: 历史验证专用 开 / 回退链 关 / 国内情报 关 / 海外情报 开 / PIT 必需。"
    )
    assert goal_switch_visibility_notes() == (
        "实时回退链已关闭；未降级不代表备用源可用。",
        "国内情报已关闭；题材/政策/资金空白不等于当天无催化。",
    )
